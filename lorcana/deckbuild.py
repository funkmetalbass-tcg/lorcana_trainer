"""Deckbuilder: evolve a legal 60-card deck from a larger card pool, scored by
win rate against a FIELD of opponent decks.

Why a GA (and not gauntlet-style single-card tweaks)
----------------------------------------------------
With ~100 pool cards and a 4-copy limit the space of legal 60-card decks is on
the order of 10^40 -- unenumerable, and unreachable by one-card hill climbing
because card value is deeply interactive (a card is good *because of* the shell
around it). A population-based search with crossover recombines whole synergy
blocks, which is what you need to escape local optima.

The binding constraint is EVALUATION COST, not search cleverness: an MCTS game
costs ~20s, so a full-MCTS GA is infeasible (thousands of decks x hundreds of
games). Hence a two-tier fitness:
    * `greedy` policy (~10ms/game) drives the GA loop -- thousands of decks
    * `mcts` re-scores only the finalists -- correcting greedy's blind spots
Greedy systematically undervalues decks needing clever sequencing (it will, for
instance, ink away location payoffs it can't plan around). The MCTS verification
pass exists precisely to catch that bias; it is not optional if you intend to
trust the winner.

Genome & legality
-----------------
An individual is a dict {card_name: copies}, copies in 1..4, summing to exactly
60, using at most 2 ink colors. Mutation/crossover may violate this; rather than
rejecting offspring (wasteful) we REPAIR them back into legality, which keeps
selection pressure on quality instead of on constraint satisfaction.
"""
import json
import os
import random
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool

from .cards import CardDB, parse_decklist
from .gauntlet import _play, make_policy

DECK_SIZE = 60
MAX_COPIES = 4
MAX_INKS = 2


# =====================================================================
# Pool loading
# =====================================================================
def load_pool(path, db, ink_pair=None):
    """Read a pool file (a decklist of any size). Returns the list of unique
    Card objects available, filtered to `ink_pair` if given."""
    cards, errors, _ = parse_decklist(path, db)
    if errors:
        for e in errors:
            print("ERROR:", e)
        sys.exit(1)
    uniq = {}
    for c in cards:
        uniq[c.name] = c
    pool = list(uniq.values())
    if ink_pair:
        want = set(i.strip().title() for i in ink_pair)
        # A dual-ink card is on-ink if it shares ANY ink with the requested pair.
        dropped = [c.name for c in pool if c.ink_types.isdisjoint(want)]
        pool = [c for c in pool if not c.ink_types.isdisjoint(want)]
        if dropped:
            print(f"Ink filter {sorted(want)}: dropped {len(dropped)} off-ink card(s): "
                  f"{', '.join(sorted(dropped)[:6])}{'...' if len(dropped) > 6 else ''}")
    return pool


def pool_capacity(pool):
    return len(pool) * MAX_COPIES


# =====================================================================
# Genome: {card_name: copies}
# =====================================================================
def genome_size(g):
    return sum(g.values())


def genome_inks(g, by_name):
    return set().union(*(by_name[n].ink_types for n in g)) if g else set()


def repair(g, pool, rng, ink_pair=None):
    """Force a genome back into legality: <=2 inks, 1..4 copies, exactly 60."""
    by_name = {c.name: c for c in pool}
    g = {n: max(0, min(MAX_COPIES, k)) for n, k in g.items() if k > 0 and n in by_name}

    # --- ink legality: keep the 2 inks with the most copies, drop the rest ---
    if ink_pair:
        keep_inks = set(i.strip().title() for i in ink_pair)
    else:
        counts = Counter()
        for n, k in g.items():
            # Credit each ink a (possibly dual-ink) card belongs to.
            for ink in by_name[n].ink_types:
                counts[ink] += k
        if not counts:
            # An EMPTY genome credits no inks, which left keep_inks empty and
            # made every pool card "off-ink" -- so repair() raised and
            # `deckbuild` without --inks died before generation 0
            # (random_genome() starts from {}). Fall back to the inks best
            # represented in the pool itself.
            for c in pool:
                for ink in c.ink_types:
                    counts[ink] += 1
        keep_inks = set(i for i, _ in counts.most_common(MAX_INKS))
    # A card is legal if it shares any ink with the kept inks.
    g = {n: k for n, k in g.items() if not by_name[n].ink_types.isdisjoint(keep_inks)}

    legal = [c for c in pool if not c.ink_types.isdisjoint(keep_inks)]
    if not legal:
        raise ValueError("no pool cards match the requested ink pair")
    if len(legal) * MAX_COPIES < DECK_SIZE:
        raise ValueError(
            f"pool too small: {len(legal)} legal cards x {MAX_COPIES} copies "
            f"= {len(legal)*MAX_COPIES} < {DECK_SIZE} required")

    # --- size: add or remove copies until exactly 60 ---
    size = genome_size(g)
    guard = 0
    while size < DECK_SIZE and guard < 10000:
        guard += 1
        # prefer topping up cards already present, else introduce a new one
        cands = [c.name for c in legal if g.get(c.name, 0) < MAX_COPIES]
        if not cands:
            break
        pick = rng.choice(cands)
        g[pick] = g.get(pick, 0) + 1
        size += 1
    while size > DECK_SIZE and guard < 20000:
        guard += 1
        cands = [n for n, k in g.items() if k > 0]
        if not cands:
            break
        pick = rng.choice(cands)
        g[pick] -= 1
        if g[pick] == 0:
            del g[pick]
        size -= 1
    return g


def random_genome(pool, rng, ink_pair=None):
    g = {}
    return repair(g, pool, rng, ink_pair)


def seeded_genomes(pool, rng, n, ink_pair=None):
    """Structure-aware seeds: build around synergy clusters (shared
    classifications and named-card dependencies) rather than pure noise, so the
    GA doesn't burn its budget climbing out of incoherent decks."""
    seeds = []
    # cluster by classification
    by_cls = defaultdict(list)
    for c in pool:
        for cl in (c.classifications or {"_none"}):
            by_cls[cl].append(c)
    clusters = [v for k, v in by_cls.items() if len(v) >= 4]
    rng.shuffle(clusters)
    for i in range(n):
        g = {}
        if clusters:
            core = clusters[i % len(clusters)]
            for c in core:
                g[c.name] = rng.randint(2, MAX_COPIES)
        seeds.append(repair(g, pool, rng, ink_pair))
    return seeds


def genome_to_deck(g, by_name):
    """Expand a genome into a flat list of Card objects (what the engine wants)."""
    deck = []
    for name, k in g.items():
        deck.extend([by_name[name]] * k)
    return deck


def genome_to_text(g):
    return "\n".join(f"{k} {n}" for n, k in sorted(g.items(), key=lambda x: -x[1]))


# =====================================================================
# Fitness (parallel, paired seeds against the field)
# =====================================================================
_W = {}


def _init_fit(db_path, field_paths):
    """Worker setup. Deliberately does NOT capture the policy or iteration
    count: those travel with each task instead, so a single Pool can serve
    both the greedy GA phase and the MCTS verification phase. Re-creating a
    Pool per generation meant every worker re-parsed the ~500KB card DB
    (~95ms each) and paid process-spawn cost, per generation."""
    db = CardDB(db_path)
    _W["db"] = db
    _W["field"] = [parse_decklist(p, db)[0] for p in field_paths]


def _fit_task(args):
    """(gid, serialized_genome, opp_idx, seed, seat0, pol, iters) -> (gid, won)"""
    gid, gser, opp_idx, seed, seat0, pol_name, iters = args
    db = _W["db"]
    by_name = db.cards
    deck = []
    for name, k in gser:
        deck.extend([by_name[name]] * k)
    deckO = _W["field"][opp_idx]
    polU = make_policy(pol_name, iters, seed=seed * 2 + 1)
    polO = make_policy(pol_name, iters, seed=seed * 2 + 2)
    won = _play(deck, deckO, polU, polO, seed, seat0)
    return (gid, won)


def evaluate_population(genomes, db_path, field_paths, games, pol, iters,
                        workers, seed0, pool_obj=None):
    """Return list of win rates (0..1), one per genome, averaged over the field.
    Every genome faces the SAME seeds, so comparisons between genomes are paired."""
    tasks = []
    for gid, g in enumerate(genomes):
        gser = tuple(sorted(g.items()))
        for opp_idx in range(len(field_paths)):
            for gi in range(games):
                tasks.append((gid, gser, opp_idx, seed0 + gi,
                              gi % 2 == 0, pol, iters))

    wins = Counter()
    played = Counter()
    initargs = (db_path, field_paths)
    # chunksize: the GA phase dispatches thousands of ~10ms greedy games, where
    # one-task-at-a-time IPC is a large fraction of the cost. MCTS verification
    # tasks run for seconds each, so those stay at chunksize=1 to avoid a
    # ragged tail (one worker grinding a batch while the others idle).
    _chunk = 1
    if pol == "greedy" and len(tasks) > workers * 4:
        _chunk = max(1, len(tasks) // (workers * 4))
    if workers == 1:
        if "db" not in _W:
            _init_fit(*initargs)
        for t in tasks:
            gid, won = _fit_task(t)
            wins[gid] += won
            played[gid] += 1
    elif pool_obj is not None:
        # Reuse the caller's long-lived Pool (see evolve()).
        for gid, won in pool_obj.imap_unordered(_fit_task, tasks, chunksize=_chunk):
            wins[gid] += won
            played[gid] += 1
    else:
        with Pool(workers, initializer=_init_fit, initargs=initargs) as pool_:
            for gid, won in pool_.imap_unordered(_fit_task, tasks, chunksize=_chunk):
                wins[gid] += won
                played[gid] += 1
    return [wins[i] / played[i] if played[i] else 0.0 for i in range(len(genomes))]


# =====================================================================
# Genetic operators
# =====================================================================
def crossover(a, b, pool, rng, ink_pair=None):
    """Blend two decks: cards in both keep ~the average count (the shared core),
    cards in one are inherited with 50% probability. This recombines whole
    synergy blocks rather than shuffling individual slots."""
    child = {}
    for name in set(a) | set(b):
        ka, kb = a.get(name, 0), b.get(name, 0)
        if ka and kb:
            child[name] = max(1, round((ka + kb) / 2))
        elif rng.random() < 0.5:
            child[name] = ka or kb
    return repair(child, pool, rng, ink_pair)


def mutate(g, pool, rng, rate, ink_pair=None):
    """Three mutation kinds: adjust a copy count, swap a card for a pool card,
    and introduce/remove a card entirely."""
    g = dict(g)
    legal = [c.name for c in pool]   # (by_name was built here and never used)
    n_mut = max(1, int(len(g) * rate))
    for _ in range(n_mut):
        r = rng.random()
        if r < 0.45 and g:                        # tweak a count
            n = rng.choice(list(g))
            g[n] = max(0, min(MAX_COPIES, g[n] + rng.choice([-1, 1])))
            if g[n] == 0:
                del g[n]
        elif r < 0.85:                            # swap one card for another
            if g:
                out = rng.choice(list(g))
                k = g.pop(out)
                cands = [n for n in legal if n not in g]
                if cands:
                    g[rng.choice(cands)] = k
                else:
                    g[out] = k
        else:                                     # introduce a new card
            cands = [n for n in legal if n not in g]
            if cands:
                g[rng.choice(cands)] = rng.randint(1, MAX_COPIES)
    return repair(g, pool, rng, ink_pair)


def tournament_select(pop, fits, rng, k=3):
    idx = max(rng.sample(range(len(pop)), min(k, len(pop))), key=lambda i: fits[i])
    return pop[idx]


# =====================================================================
# The GA loop
# =====================================================================
def evolve(db_path, pool_path, field_paths, ink_pair,
           generations=15, pop_size=24, games=8, elite=4,
           mut_rate=0.25, pol="greedy", iters=1, workers=None,
           seed=0, checkpoint=None, verify_games=40, verify_iters=100,
           verify_top=3, out_path=None):
    rng = random.Random(seed)
    db = CardDB(db_path)
    pool = load_pool(pool_path, db, ink_pair)
    by_name = {c.name: c for c in pool}
    workers = workers or os.cpu_count() or 1

    cap = pool_capacity(pool)
    print(f"Pool: {len(pool)} unique legal cards (capacity {cap} >= {DECK_SIZE} required)")
    if cap < DECK_SIZE:
        print("ERROR: pool cannot make a legal 60-card deck.")
        sys.exit(1)

    # --- resume? ---
    start_gen = 0
    pop = None
    if checkpoint and os.path.exists(checkpoint):
        try:
            with open(checkpoint) as f:
                st = json.load(f)
            if st.get("pool_path") == os.path.abspath(pool_path):
                pop = [dict(g) for g in st["population"]]
                start_gen = st["generation"]
                print(f"Resuming deckbuild from generation {start_gen}")
        except Exception:
            pop = None

    if pop is None:
        pop = seeded_genomes(pool, rng, pop_size // 2, ink_pair)
        pop += [random_genome(pool, rng, ink_pair) for _ in range(pop_size - len(pop))]

    # One Pool for the entire run: the GA generations, the final scoring pass
    # and the MCTS verification all share it. Workers load the card DB once
    # instead of once per generation.
    _pool = None
    if workers > 1:
        _pool = Pool(workers, initializer=_init_fit,
                     initargs=(db_path, field_paths))
    try:
        best_g, best_f = None, -1.0
        for gen in range(start_gen, generations):
            fits = evaluate_population(pop, db_path, field_paths, games, pol, iters,
                                       workers, seed0=seed + gen * 1000,
                                       pool_obj=_pool)
            order = sorted(range(len(pop)), key=lambda i: -fits[i])
            if fits[order[0]] > best_f:
                best_f, best_g = fits[order[0]], dict(pop[order[0]])
            mean_f = sum(fits) / len(fits)
            print(f"  gen {gen:2d}/{generations}  best {100*fits[order[0]]:.0f}%  "
                  f"mean {100*mean_f:.0f}%  (pop {len(pop)}, {games} games/deck)")

            # next generation: elites + offspring
            nxt = [dict(pop[i]) for i in order[:elite]]
            while len(nxt) < pop_size:
                pa = tournament_select(pop, fits, rng)
                pb = tournament_select(pop, fits, rng)
                child = crossover(pa, pb, pool, rng, ink_pair)
                child = mutate(child, pool, rng, mut_rate, ink_pair)
                nxt.append(child)
            pop = nxt

            if checkpoint:
                tmp = checkpoint + ".tmp"
                with open(tmp, "w") as f:
                    json.dump({"pool_path": os.path.abspath(pool_path),
                               "generation": gen + 1,
                               "population": pop}, f)
                os.replace(tmp, checkpoint)

        # --- final scoring of the last population, then MCTS verification ---
        fits = evaluate_population(pop, db_path, field_paths, games, pol, iters,
                                   workers, seed0=seed + 999999, pool_obj=_pool)
        order = sorted(range(len(pop)), key=lambda i: -fits[i])
        finalists = [pop[i] for i in order[:verify_top]]
        if best_g is not None and not any(g == best_g for g in finalists):
            finalists.append(best_g)

        print(f"\nVerifying {len(finalists)} finalist(s) with MCTS "
              f"({verify_games} games/opponent, {verify_iters} iters)... this is the slow part.")
        vfits = evaluate_population(finalists, db_path, field_paths, verify_games,
                                    "mcts", verify_iters, workers, seed0=seed + 7,
                                    pool_obj=_pool)
    finally:
        if _pool is not None:
            _pool.close()
            _pool.join()
    vorder = sorted(range(len(finalists)), key=lambda i: -vfits[i])
    champion = finalists[vorder[0]]

    report = _format_report(finalists, vfits, vorder, by_name, field_paths,
                            pol, games, verify_games, verify_iters)
    print(report)
    if out_path:
        with open(out_path, "w") as f:
            f.write(genome_to_text(champion) + "\n")
        print(f"\nChampion decklist written to {out_path}")
    return champion, vfits[vorder[0]], report


def _format_report(finalists, vfits, vorder, by_name, field_paths,
                   pol, games, vgames, viters):
    L = []
    L.append("\n" + "=" * 74)
    L.append("DECKBUILD RESULT (MCTS-verified)")
    L.append("=" * 74)
    L.append(f"\nSearch fitness: {pol} @ {games} games/opponent")
    L.append(f"Verification:   mcts @ {vgames} games/opponent, {viters} iters")
    L.append(f"Field:          {', '.join(os.path.basename(p) for p in field_paths)}")
    L.append("\nFinalists (MCTS win rate vs. the field):")
    for rank, i in enumerate(vorder, 1):
        L.append(f"  {rank}. {100*vfits[i]:5.1f}%   ({len(finalists[i])} unique cards)")
    champ = finalists[vorder[0]]
    L.append("\nCHAMPION DECKLIST (60 cards):")
    for name, k in sorted(champ.items(), key=lambda x: (-x[1], x[0])):
        c = by_name[name]
        L.append(f"  {k} {name}  [{c.ink_type} {c.cost}]")
    L.append(f"\n  total: {sum(champ.values())} cards, "
             f"inks: {sorted(set().union(*(by_name[n].ink_types for n in champ)) if champ else set())}")
    L.append("\nCAVEATS")
    L.append("  * The GA searched with a WEAK policy; greedy undervalues decks that")
    L.append("    need clever sequencing. The MCTS pass re-ranks finalists but cannot")
    L.append("    recover a strong deck the search never explored.")
    L.append("  * This is a local optimum tuned to THIS field. A deck that beats these")
    L.append("    opponents may fold to a different one. Re-run with a wider field.")
    L.append("  * Confirm the champion with `gauntlet` before trusting it, and use")
    L.append("    `analyze` to check its curve and loss patterns.")
    return "\n".join(L)
