"""Command-line interface.

  manifest              audit every implemented card + assumptions
  sim                   batch AI-vs-AI games, win-rate report
  watch                 one verbose game
  play                  interactive trainer: you pilot a deck vs MCTS, `hint` for advice
"""
import argparse, os, random, sys, time

from .cards import CardDB, parse_decklist
from .engine import Game
from .policies import default_mulligan, random_policy, greedy_policy
from . import abilities, mcts


def load(db_path, a_path, b_path):
    db = CardDB(db_path)
    deckA, errA, warnA = parse_decklist(a_path, db)
    deckB, errB, warnB = parse_decklist(b_path, db)
    for e in errA + errB:
        print("ERROR:", e); sys.exit(1)
    for w in warnA + warnB:
        print("WARNING:", w)
    used = {c.name for c in deckA + deckB}
    for kept, dups in sorted(db.name_collisions.items()):
        if kept in used:
            print(f"WARNING: {db_path} contains duplicate entries differing only "
                  f"by case: '{kept}' and {dups}. Using '{kept}'; remove the "
                  f"duplicate from the JSON.")
    return db, deckA, deckB


def fmt_action(game, a):
    k = a[0]
    if k == "pass": return "End turn"
    if k == "ink": return f"Ink {a[1]}"
    if k == "quest":
        ch = game.chars[a[1]]
        s = f"Quest {ch.card.name} (+{game.eff_lore(ch)} lore)"
        if len(a) > 2 and a[2] == "sh_banish":
            s += " [banish Sleepy Hollow: +2 lore & Evasive]"
        elif len(a) > 2 and a[2] == "sh_keep":
            s += " [keep Sleepy Hollow]"
        return s
    if k == "challenge":
        att = game.chars[a[1]]
        tgt = (game.chars.get(a[3]) or game.locs.get(a[3]))
        return f"Challenge: {att.card.base_name} -> {tgt.card.name if tgt else '?'}"
    if k == "move":
        s = f"Move {game.chars[a[1]].card.base_name} -> {game.locs[a[2]].card.base_name}"
        if len(a) > 3 and a[3] == "zoo_draw":
            s += " [Zootopia: draw then discard]"
        elif len(a) > 3 and a[3] == "zoo_skip":
            s += " [Zootopia: no draw]"
        return s
    if k == "sing": return f"Sing {a[1]}"
    if k == "sing_together": return f"Sing Together {a[1]}"
    if k == "play":
        s = f"Play {a[1]}"
        pd = dict(a[2]) if len(a) > 2 and a[2] else {}
        bits = []
        for key, v in pd.items():
            if v in (None, False): continue
            if key == "shift": bits.append(f"shift onto #{v}")
            elif key == "exerted": bits.append("enter exerted")
            elif key == "scheme": bits.append("draw (opp +2 lore)")
            elif key == "ret": bits.append(f"return {v}")
            elif key == "free": bits.append(f"free-play {v}")
            elif key == "loc_id": bits.append(f"protect {game.locs[v].card.base_name}" if v in game.locs else "")
            elif key == "loc" and isinstance(v, int): bits.append(f"to {game.locs[v].card.base_name}" if v in game.locs else "")
            elif key == "loc": bits.append(f"from discard: {v}")
            elif key == "char": bits.append(f"move {game.chars[v].card.base_name}" if v in game.chars else "")
        if bits: s += " [" + ", ".join(b for b in bits if b) + "]"
        return s
    return str(a)


def show_board(game, viewer):
    for p in (0, 1):
        pl = game.players[p]
        tag = "YOU" if p == viewer else "OPP"
        print(f"\n[{tag}] P{p}  lore {pl.lore}/20  ink {pl.ink_ready}/{pl.ink_total}  "
              f"hand {len(pl.hand)}  deck {len(pl.deck)}  discard {len(pl.discard)}")
        for loc in game.my_locs(p):
            occupants = [c.card.base_name for c in game.chars.values() if c.location == loc.uid]
            print(f"   LOC #{loc.uid} {loc.card.name}  W{loc.card.willpower - loc.damage}/"
                  f"{loc.card.willpower}  lore/turn {game.loc_lore(loc)}"
                  + (f"  <{', '.join(occupants)}>" if occupants else ""))
        for ch in game.my_chars(p):
            st = []
            if ch.exerted: st.append("EXERTED")
            if not game.is_dry(ch): st.append("drying")
            if ch.location: st.append(f"@{game.locs[ch.location].card.base_name}" if ch.location in game.locs else "")
            print(f"   #{ch.uid} {ch.card.name}  {game.eff_strength(ch)}/"
                  f"{game.eff_willpower(ch) - ch.damage}(of {game.eff_willpower(ch)}) "
                  f"L{game.eff_lore(ch)} {' '.join(st)}")
    pl = game.players[viewer]
    print(f"\nYour hand: " + ", ".join(
        f"{c.name}({game.play_cost(viewer, c)}{'' if c.inkable else ',NI'})" for c in pl.hand))


def cmd_manifest(args):
    db, deckA, deckB = load(args.db, args.deck_a, args.deck_b)
    print("=" * 70)
    print("IMPLEMENTED CARDS (audit ability translations against real cards)")
    seen = set()
    for label, deck in (("DECK A", deckA), ("DECK B", deckB)):
        print(f"\n--- {label} ---")
        for c in deck:
            if c.name in seen: continue
            seen.add(c.name)
            flag = "  ** PLACEHOLDER STATS **" if c.placeholder else ""
            print(f"  {c.name} [{c.card_type} {c.cost}ink"
                  + (f" {c.strength}/{c.willpower} L{c.lore}" if c.is_character else "")
                  + (f" W{c.willpower} L{c.lore} move{c.move_cost}" if c.is_location else "")
                  + ("" if c.inkable else " NONINKABLE") + "]" + flag)
    print("\n" + "=" * 70)
    print("ASSUMPTIONS & HEURISTIC SIMPLIFICATIONS")
    for i, a in enumerate(abilities.ASSUMPTIONS, 1):
        print(f"\n{i}. {a}")


def make_policy(name, iters, seed):
    if name == "random":
        return lambda g, rng: random_policy(g, rng)
    if name == "greedy":
        return lambda g, rng: greedy_policy(g, rng)
    if name == "mcts":
        return mcts.mcts_policy_factory(iterations=iters, seed=seed)
    raise ValueError(name)


def run_game(deckA, deckB, polA, polB, seed=None, log=None, turn_cap=120, first=None):
    g = Game(deckA, deckB, seed=seed, log=log)
    if first is not None and first == 1:
        # swap seats so 'first' player starts; simpler: Game always starts P0,
        # so alternate by swapping decks & policies outside. Here: no-op.
        pass
    g.start(mulligan_fn=lambda game, p: default_mulligan(game, p))
    rng = random.Random(seed)
    while g.winner is None and g.turn < turn_cap:
        pol = polA if g.active == 0 else polB
        a = pol(g, rng)
        if log is not None:
            log.append(f"P{g.active}: {fmt_action(g, a)}")
        g.apply(a)
    if g.winner is None:  # turn cap: decide on lore
        if g.players[0].lore != g.players[1].lore:
            g.winner = 0 if g.players[0].lore > g.players[1].lore else 1
    return g


def cmd_sim(args):
    db, deckA, deckB = load(args.db, args.deck_a, args.deck_b)
    enforce_coverage([("DECK A", args.deck_a, deckA),
                      ("DECK B", args.deck_b, deckB)],
                     override=getattr(args, "allow_unimplemented", False))
    polA = make_policy(args.a, args.iters, seed=1)
    polB = make_policy(args.b, args.iters, seed=2)
    wins = [0, 0]
    lore_diff = 0
    turns = []
    t0 = time.time()
    for i in range(args.games):
        # alternate who goes first by swapping decks each game
        if i % 2 == 0:
            g = run_game(deckA, deckB, polA, polB, seed=args.seed + i)
            w = g.winner
            la, lb = g.players[0].lore, g.players[1].lore
        else:
            g = run_game(deckB, deckA, polB, polA, seed=args.seed + i)
            w = 1 - g.winner if g.winner is not None else None
            la, lb = g.players[1].lore, g.players[0].lore
        if w is not None:
            wins[w] += 1
        lore_diff += la - lb
        turns.append(g.turn)
    dt = time.time() - t0
    n = args.games
    print(f"\n{n} games in {dt:.1f}s ({dt/n:.2f}s/game)  |  Deck A policy: {args.a}, Deck B policy: {args.b}")
    print(f"Deck A wins: {wins[0]} ({100*wins[0]/n:.0f}%)   Deck B wins: {wins[1]} ({100*wins[1]/n:.0f}%)")
    print(f"Avg lore diff (A-B): {lore_diff/n:+.1f}   Avg game length: {sum(turns)/n:.1f} half-turns")


def cmd_watch(args):
    db, deckA, deckB = load(args.db, args.deck_a, args.deck_b)
    enforce_coverage([("DECK A", args.deck_a, deckA),
                      ("DECK B", args.deck_b, deckB)],
                     override=getattr(args, "allow_unimplemented", False))
    polA = make_policy(args.a, args.iters, seed=11)
    polB = make_policy(args.b, args.iters, seed=22)
    log = []
    g = run_game(deckA, deckB, polA, polB, seed=args.seed, log=log)
    print("\n".join(log))
    print(f"\nWINNER: {'Deck A' if g.winner == 0 else 'Deck B'}  "
          f"(lore {g.players[0].lore} - {g.players[1].lore}, {g.turn} half-turns)")


def cmd_play(args):
    db, deckA, deckB = load(args.db, args.deck_a, args.deck_b)
    enforce_coverage([("DECK A", args.deck_a, deckA),
                      ("DECK B", args.deck_b, deckB)],
                     override=getattr(args, "allow_unimplemented", False))
    human = 0 if args.human.upper() == "A" else 1
    ai = 1 - human
    ai_pol = mcts.mcts_policy_factory(iterations=args.iters, seed=args.seed)
    g = Game(deckA, deckB, seed=args.seed, log=[])
    g.start(mulligan_fn=lambda game, p: default_mulligan(game, p))
    rng = random.Random(args.seed)
    print("\nInteractive trainer. Commands: number = take action, 'hint' = MCTS advice,")
    print("'board' = redisplay, 'log' = recent events, 'quit' = exit.\n")
    while g.winner is None:
        if g.active == ai:
            a = ai_pol(g, rng)
            print(f"\n>>> AI: {fmt_action(g, a)}")
            g.apply(a)
            continue
        show_board(g, human)
        acts = g.legal_actions()
        print("\nYour options:")
        for i, a in enumerate(acts):
            print(f"  {i:2d}. {fmt_action(g, a)}")
        cmd = input("\n> ").strip().lower()
        if cmd == "quit":
            return
        if cmd == "board":
            continue
        if cmd == "log":
            print("\n".join(g.log[-25:])); continue
        if cmd == "hint":
            print(f"Thinking ({args.iters} iterations)...")
            _, ranked = mcts.search(g, iterations=args.iters, rng=rng, perspective=human)
            print("MCTS ranking (visits ~ confidence, value = est. win prob):")
            for a, vis, val in ranked[:8]:
                print(f"   {val:5.2f}  ({vis:4d} visits)  {fmt_action(g, a)}")
            continue
        try:
            g.apply(acts[int(cmd)])
        except (ValueError, IndexError):
            print("?")
    print(f"\nGAME OVER -- {'You win!' if g.winner == human else 'AI wins.'} "
          f"(lore {g.players[0].lore} - {g.players[1].lore})")


def cmd_analyze(args):
    from . import analyze
    db, deckA, deckB = load(args.db, args.deck_a, args.deck_b)
    enforce_coverage([("DECK A", args.deck_a, deckA),
                      ("DECK B", args.deck_b, deckB)],
                     override=getattr(args, "allow_unimplemented", False))
    if args.study == "A":
        deckU, deckO, lu, lo = deckA, deckB, "Deck A", "Deck B"
    else:
        deckU, deckO, lu, lo = deckB, deckA, "Deck B", "Deck A"
    print(f"Analyzing {lu} ({args.a}) vs {lo} ({args.b}), "
          f"{args.games} games at {args.iters} iters... this can take a while.")
    t0 = time.time()
    R = analyze.analyze_deck(db, deckU, deckO, args.a, args.b,
                             args.games, args.iters, args.seed,
                             label_u=lu, label_o=lo)
    report = analyze.format_report(R)
    print(report)
    if getattr(args, "out", None):
        with open(args.out, "w") as f:
            f.write(report + "\n")
        print(f"\n(report written to {args.out})")
    if args.suggest:
        # gather flagged cut candidates: negative-delta or low-utilization cards
        cands = []
        for name in R["unique"]:
            gp = R["games_when_played"][name]
            gu = R["games_when_unseen"][name]
            if gp and gu:
                d = (100.0*R["wins_when_played"][name]/gp) - (100.0*R["wins_when_unseen"][name]/gu)
                if d <= -8:
                    cands.append((d, name))
        cands.sort()
        names = [n for _, n in cands][:5]
        if names:
            print(analyze.suggest_swaps(db, deckU, deckO, args.a, args.b,
                                        max(60, args.games // 2), args.iters,
                                        args.seed + 9999, names, label_u=lu))
        else:
            print("\n--suggest: no cards tripped the cut threshold to A/B test.")
    print(f"\n(analysis took {time.time()-t0:.0f}s)")


COVERAGE_MAX_UNIMPL_PCT = 5.0   # gate: refuse to simulate above this


def card_sources(card):
    """Return (list_of_sources, covered_bool) for one Card."""
    from . import schema
    sources = []
    if card.name in abilities.HAND_IMPLEMENTED:
        sources.append("python")
    if card.name in schema.registry_manual_names():
        sources.append("schema-manual")
    elif schema.has_schema_entry(card.name) and \
            any(e.get("trigger") for e in schema.registry().get(card.name, [])):
        sources.append("schema-auto")
    if card.keywords:
        sources.append("keywords:" + ",".join(sorted(card.keywords)))
    if not card.text.strip():
        sources.append("vanilla")
    covered = ("python" in sources or "schema-manual" in sources
               or "schema-auto" in sources or not card.residual)
    return sources, covered


def deck_coverage(deck):
    """(unimplemented_copies, total_copies, pct, {name: copies}) for a decklist."""
    total = len(deck)
    bad = {}
    for c in deck:
        _, covered = card_sources(c)
        if not covered:
            bad[c.name] = bad.get(c.name, 0) + 1
    n_bad = sum(bad.values())
    pct = (100.0 * n_bad / total) if total else 0.0
    return n_bad, total, pct, bad


def enforce_coverage(named_decks, max_pct=None, override=False):
    """Refuse to run simulations on decks whose unimplemented share exceeds the
    gate. named_decks: list of (label, path_or_name, deck_cards)."""
    limit = COVERAGE_MAX_UNIMPL_PCT if max_pct is None else max_pct
    failures = []
    for label, name, deck in named_decks:
        n_bad, total, pct, bad = deck_coverage(deck)
        if pct > limit:
            failures.append((label, name, n_bad, total, pct, bad))
    if not failures:
        return
    print("\n" + "=" * 78)
    print(f"COVERAGE GATE FAILED (limit: {limit:.0f}% of copies unimplemented)")
    print("=" * 78)
    for label, name, n_bad, total, pct, bad in failures:
        print(f"\n  {label}: {name}")
        print(f"    {n_bad}/{total} copies ({pct:.0f}%) have text the engine ignores:")
        for cname, cnt in sorted(bad.items(), key=lambda kv: -kv[1]):
            print(f"      {cnt}x {cname}")
    print("\nSimulating these decks would silently ignore that text and produce")
    print("win rates that do not reflect the real cards. Implement the abilities")
    print("(abilities.py / abilities_manual.json), or re-run with --allow-unimplemented")
    print("if you understand the results are unreliable.")
    if not override:
        sys.exit(2)
    print("\n--allow-unimplemented set: continuing anyway. RESULTS ARE UNRELIABLE.\n")


def cmd_coverage(args):
    """Per-card implementation-source report for both decks; the gate that
    keeps unimplemented cards visible (Phase 2/4 of the generalization)."""
    from . import schema
    db, deckA, deckB = load(args.db, args.deck_a, args.deck_b)
    any_unimpl = False
    for label, deck in (("DECK A", deckA), ("DECK B", deckB)):
        print(f"\n=== {label} ===")
        seen = set()
        for c in deck:
            if c.name in seen:
                continue
            seen.add(c.name)
            sources = []
            if c.name in abilities.HAND_IMPLEMENTED:
                sources.append("python")
            if c.name in schema.registry_manual_names():
                sources.append("schema-manual")
            elif schema.has_schema_entry(c.name) and \
                    any(e.get("trigger") for e in schema.registry().get(c.name, [])):
                sources.append("schema-auto")
            if c.keywords:
                sources.append("keywords:" + ",".join(sorted(c.keywords)))
            if not c.text.strip():
                sources.append("vanilla")
            covered = ("python" in sources or "schema-manual" in sources
                       or "schema-auto" in sources or not c.residual)
            status = "ok          " if covered else "UNIMPLEMENTED"
            if not covered:
                any_unimpl = True
            print(f"  [{status}] {c.name[:44]:44s} {' + '.join(sources) or '-'}")
            if not covered:
                print(f"                unhandled text: {c.residual[:90]}")
    print()
    for label, deck in (("DECK A", deckA), ("DECK B", deckB)):
        n_bad, total, pct, _ = deck_coverage(deck)
        verdict = "PASS" if pct <= COVERAGE_MAX_UNIMPL_PCT else "FAIL"
        print(f"  {label}: {n_bad}/{total} copies unimplemented ({pct:.0f}%)  "
              f"[gate {COVERAGE_MAX_UNIMPL_PCT:.0f}%: {verdict}]")
    if any_unimpl:
        print("\nWARNING: deck(s) contain UNIMPLEMENTED cards -- simulation "
              "results will silently ignore that text. Add schema entries or "
              "Python logic before trusting win rates.")
    else:
        print("\nAll deck cards covered (python / schema / keywords / vanilla).")


def cmd_gauntlet(args):
    from . import gauntlet, analyze
    db, deckA, deckB = load(args.db, args.deck_a, args.deck_b)
    field_paths = args.field
    if not field_paths:
        print("ERROR: --field requires at least one opponent decklist path")
        sys.exit(1)
    field_labels = [os.path.splitext(os.path.basename(p))[0] for p in field_paths]

    # Coverage gate: deck A plus every field deck must be implemented, or the
    # win rates are measuring cards that don't do what they say.
    checks = [("DECK A", args.deck_a, deckA)]
    for fp, fl in zip(field_paths, field_labels):
        fdeck, ferr, fwarn = parse_decklist(fp, db)
        for e in ferr:
            print("ERROR:", e); sys.exit(1)
        checks.append((f"FIELD {fl}", fp, fdeck))
    enforce_coverage(checks, override=getattr(args, "allow_unimplemented", False))

    # Resolve candidate cuts
    uniqueA = []
    seen = set()
    for c in deckA:
        if c.name not in seen:
            seen.add(c.name); uniqueA.append(c.name)

    if args.candidates_file:
        with open(args.candidates_file) as f:
            wanted = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        candidates = [n for n in wanted if n in seen]
        missing = [n for n in wanted if n not in seen]
        for m in missing:
            print(f"WARNING: candidate '{m}' is not in Deck A; skipping")
    elif args.candidates == "all":
        candidates = uniqueA
    else:  # auto: reuse analyze drag-flags against the first field deck
        print("Selecting candidates via a quick analysis pass (auto mode)...")
        deckO0, _, _ = parse_decklist_path(field_paths[0], db)
        R = analyze.analyze_deck(db, deckA, deckO0, args.a, args.b,
                                 max(20, args.games // 4), args.iters, args.seed,
                                 progress=None)
        cands = []
        for name in R["unique"]:
            gp = R["games_when_played"][name]; gu = R["games_when_unseen"][name]
            if gp and gu:
                d = (R["wins_when_played"][name] / gp) - (R["wins_when_unseen"][name] / gu)
                if d <= -0.08:
                    cands.append((d, name))
        cands.sort()
        candidates = [n for _, n in cands][:args.max_candidates]
        if not candidates:
            print("No drag candidates found in auto mode; use --candidates all "
                  "or --candidates-file.")
            return
        print(f"Auto-selected {len(candidates)} candidate cuts: {candidates}")

    import time
    t0 = time.time()
    workers = args.workers if args.workers is not None else (os.cpu_count() or 1)
    print(f"\nRunning gauntlet: {len(candidates)} candidate(s) + baseline x "
          f"{len(field_paths)} opponent(s) x {args.games} games "
          f"= {(len(candidates)+1)*len(field_paths)*args.games} games on {workers} workers.")
    # Partial results are written to --out (or --partial-out) on every progress
    # tick, so a stalled or killed run still leaves usable standings on disk.
    partial_path = args.partial_out or args.out
    R = gauntlet.run_gauntlet(
        args.db, args.deck_a, field_paths, candidates,
        games=args.games, iters=args.iters, a_pol=args.a, b_pol=args.b,
        workers=workers, seed0=args.seed, progress=args.progress_every,
        checkpoint=args.checkpoint, resume=not args.no_resume,
        partial_out=partial_path, field_labels=field_labels)
    report = gauntlet.format_gauntlet(R, field_labels)
    print(report)
    if args.out:
        with open(args.out, "w") as f:
            f.write(report + "\n")
        print(f"\n(report written to {args.out})")
    print(f"\n(gauntlet took {time.time()-t0:.0f}s on {workers} workers)")


def parse_decklist_path(path, db):
    from .cards import parse_decklist as _pd
    return _pd(path, db)


def cmd_deckbuild(args):
    from . import deckbuild
    import time
    t0 = time.time()
    ink_pair = [i.strip() for i in args.inks.split(",")] if args.inks else None
    if ink_pair and len(ink_pair) > 2:
        print("ERROR: at most 2 ink colors"); sys.exit(1)
    workers = args.workers if args.workers is not None else (os.cpu_count() or 1)
    print(f"Evolving a {deckbuild.DECK_SIZE}-card deck from {args.pool}")
    print(f"  field: {', '.join(args.field)}")
    print(f"  {args.generations} generations x {args.pop} population, "
          f"{args.games} games/deck on {workers} workers")
    deckbuild.evolve(
        args.db, args.pool, args.field, ink_pair,
        generations=args.generations, pop_size=args.pop, games=args.games,
        elite=args.elite, mut_rate=args.mut_rate, pol=args.fitness,
        iters=args.fit_iters, workers=workers, seed=args.seed,
        checkpoint=args.checkpoint, verify_games=args.verify_games,
        verify_iters=args.verify_iters, verify_top=args.verify_top,
        out_path=args.out)
    print(f"\n(deckbuild took {time.time()-t0:.0f}s)")


def main():
    ap = argparse.ArgumentParser(prog="lorcana-trainer")
    ap.add_argument("--db", default="master_legal_cardlist.json")
    ap.add_argument("--deck-a", default="deckA.txt")
    ap.add_argument("--deck-b", default="deckB.txt")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("manifest")
    sub.add_parser("coverage")

    s = sub.add_parser("sim")
    s.add_argument("--allow-unimplemented", action="store_true",
                   help="run even if decks exceed the coverage gate "
                        "(results will be unreliable)")
    s.add_argument("--games", type=int, default=100)
    s.add_argument("--a", default="greedy", choices=["random", "greedy", "mcts"])
    s.add_argument("--b", default="greedy", choices=["random", "greedy", "mcts"])
    s.add_argument("--iters", type=int, default=150)
    s.add_argument("--seed", type=int, default=0)

    s = sub.add_parser("watch")
    s.add_argument("--allow-unimplemented", action="store_true",
                   help="run even if decks exceed the coverage gate "
                        "(results will be unreliable)")
    s.add_argument("--a", default="greedy", choices=["random", "greedy", "mcts"])
    s.add_argument("--b", default="greedy", choices=["random", "greedy", "mcts"])
    s.add_argument("--iters", type=int, default=150)
    s.add_argument("--seed", type=int, default=1)

    s = sub.add_parser("play")
    s.add_argument("--allow-unimplemented", action="store_true",
                   help="run even if decks exceed the coverage gate "
                        "(results will be unreliable)")
    s.add_argument("--human", default="A")
    s.add_argument("--iters", type=int, default=400)
    s.add_argument("--seed", type=int, default=None)

    s = sub.add_parser("analyze")
    s.add_argument("--allow-unimplemented", action="store_true",
                   help="run even if decks exceed the coverage gate "
                        "(results will be unreliable)")
    s.add_argument("--study", default="A", choices=["A", "B"],
                   help="which deck to study (default A)")
    s.add_argument("--games", type=int, default=200)
    s.add_argument("--a", default="mcts", choices=["random", "greedy", "mcts"],
                   help="policy for the studied deck")
    s.add_argument("--b", default="mcts", choices=["random", "greedy", "mcts"],
                   help="policy for the opposing deck")
    s.add_argument("--iters", type=int, default=150)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--suggest", action="store_true",
                   help="A/B test trimming each flagged cut candidate")
    s.add_argument("--out", default=None,
                   help="also write the report to this file")

    s = sub.add_parser("gauntlet")
    s.add_argument("--allow-unimplemented", action="store_true",
                   help="run even if decks exceed the coverage gate "
                        "(results will be unreliable)")
    s.add_argument("--field", nargs="+", required=True,
                   help="one or more opponent decklist paths (DeckB1 DeckB2 ...)")
    s.add_argument("--candidates", default="auto", choices=["auto", "all"],
                   help="auto = analyze drag-flags (default); all = every Deck A card")
    s.add_argument("--candidates-file", default=None,
                   help="file of card names to test as cuts (overrides --candidates)")
    s.add_argument("--max-candidates", type=int, default=5,
                   help="cap for auto mode (default 5)")
    s.add_argument("--games", type=int, default=100)
    s.add_argument("--iters", type=int, default=100)
    s.add_argument("--a", default="mcts", choices=["random", "greedy", "mcts"])
    s.add_argument("--b", default="mcts", choices=["random", "greedy", "mcts"])
    s.add_argument("--workers", type=int, default=None,
                   help="parallel worker processes (default: CPU count; 1 = serial)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", default=None)
    s.add_argument("--checkpoint", default=None,
                   help="path to a resumable checkpoint file; if it exists and "
                        "matches this run, completed games are skipped")
    s.add_argument("--no-resume", action="store_true",
                   help="ignore an existing checkpoint and start fresh "
                        "(overwrites it)")
    s.add_argument("--progress-every", type=int, default=1,
                   help="update the counter and rewrite partial results every N "
                        "games (default 1 = every game)")
    s.add_argument("--partial-out", default=None,
                   help="where to write live partial results (default: --out path)")

    s = sub.add_parser("deckbuild")
    s.add_argument("--pool", required=True,
                   help="card pool file (a decklist of any size, e.g. 100 cards)")
    s.add_argument("--field", nargs="+", required=True,
                   help="opponent decklists to optimize against")
    s.add_argument("--inks", default=None,
                   help="comma-separated ink pair, e.g. 'Ruby,Steel' (recommended)")
    s.add_argument("--generations", type=int, default=15)
    s.add_argument("--pop", type=int, default=24, help="population size")
    s.add_argument("--games", type=int, default=8,
                   help="games per deck per opponent during the GA search")
    s.add_argument("--elite", type=int, default=4)
    s.add_argument("--mut-rate", type=float, default=0.25)
    s.add_argument("--fitness", default="greedy", choices=["greedy", "mcts"],
                   help="search policy (greedy is ~1000x faster; default)")
    s.add_argument("--fit-iters", type=int, default=1,
                   help="MCTS iters if --fitness mcts")
    s.add_argument("--verify-games", type=int, default=40)
    s.add_argument("--verify-iters", type=int, default=100)
    s.add_argument("--verify-top", type=int, default=3)
    s.add_argument("--workers", type=int, default=None)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--checkpoint", default=None)
    s.add_argument("--out", default=None, help="write the champion decklist here")

    args = ap.parse_args()
    {"manifest": cmd_manifest, "sim": cmd_sim, "watch": cmd_watch,
     "play": cmd_play, "analyze": cmd_analyze,
     "coverage": cmd_coverage, "gauntlet": cmd_gauntlet,
     "deckbuild": cmd_deckbuild}[args.cmd](args)


if __name__ == "__main__":
    main()
