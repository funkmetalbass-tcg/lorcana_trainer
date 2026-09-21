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
import statistics
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

from .cards import CardDB, parse_decklist
from .gauntlet import _play, make_policy

DECK_SIZE = 60
MAX_COPIES = 4
MAX_INKS = 2

# Cards every genome must contain, at the given minimum copy count. Enforced in
# repair(), which is the single chokepoint all genomes pass through (random,
# seeded, crossover, mutate all end by calling it). The name must match the DB
# card name EXACTLY or the force silently does nothing.
FORCE = {
    "Powerline - World's Greatest Rock Star": 4,   # Ruby, 6-cost
    "Max Goof - Chart Topper": 4,                  # Emerald, 6-cost
}

# --- CATEGORY QUOTAS -------------------------------------------------------
# Minimum COPIES (not distinct names) of a whole category, enforced in repair()
# alongside FORCE. 0 disables a quota.
#
# Why these exist: fitness is measured by a greedy policy that evaluates one
# turn at a time, so it systematically undervalues cards whose payoff is
# structural rather than immediate. A song is only good if you have a singer
# two turns later; a 1-cost body is only good because it makes turn-3 singing
# possible at all. Greedy inks both away, and the GA then never explores the
# region of deck-space where the archetype works. A hard floor is a blunt fix
# for a biased signal -- expect fitness to push back, i.e. these floors will
# sit exactly AT their minimum rather than finding their own level.
MIN_SONGS = 12       # song copies (Actions with the Song classification)
MIN_ONE_DROPS = 4    # 1-cost Character/Location/Item copies (NOT actions)

_FORCE_WARNED = {}   # warn-once per missing/illegal forced card or unmeetable quota


def _quota_specs(legal):
    """Build the active quota list for an ink-legal card set.

    Returns [(label, members_set, floor)]. Membership is derived from `legal`,
    so a quota can only ever pull in ink-legal pool cards.

    Members come from a SORTED list before being setified: rng.choice over a
    set would make card selection depend on string hash order, silently
    breaking --seed reproducibility across runs."""
    raw = []
    if MIN_SONGS > 0:
        raw.append(("song",
                    set(sorted(c.name for c in legal if c.is_song)),
                    MIN_SONGS))
    if MIN_ONE_DROPS > 0:
        raw.append(("1-cost permanent",
                    set(sorted(c.name for c in legal if c.cost == 1
                               and c.card_type in ("Character", "Location", "Item"))),
                    MIN_ONE_DROPS))
    # Clamp each floor to what the pool can physically supply. An unreachable
    # floor would otherwise mark every member permanently un-trimmable, since
    # the count can never climb to the floor that lifts the protection.
    return [(label, members, min(floor, len(members) * MAX_COPIES))
            for label, members, floor in raw]


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

    # --- forced minimums: guarantee required cards at their floor ---
    # Applied after ink-legality so a forced card must be a legal pool card that
    # survives the ink filter; a forced card absent from `legal` is skipped
    # (with a one-time warning) rather than silently reintroduced as illegal.
    forced = {}
    for name, floor in FORCE.items():
        if name in by_name and any(c.name == name for c in legal):
            fl = min(MAX_COPIES, max(1, floor))
            forced[name] = fl
            g[name] = max(g.get(name, 0), fl)
        elif name in by_name and not _FORCE_WARNED.get(name):
            print(f"WARNING: forced card {name!r} is not ink-legal for this "
                  f"pool/inks; not forcing it.")
            _FORCE_WARNED[name] = True
        elif name not in by_name and not _FORCE_WARNED.get(name):
            print(f"WARNING: forced card {name!r} not found in pool; "
                  f"not forcing it.")
            _FORCE_WARNED[name] = True

    # --- category quotas: songs, 1-cost permanents ---
    # Placed after ink-legality and after FORCE, so a forced card that happens
    # to be a quota member already counts toward its floor.
    _quotas = _quota_specs(legal)

    def _qcount(gg, members):
        return sum(k for n, k in gg.items() if n in members)

    def _protected(gg, reduce_by=1):
        """Names that must not lose `reduce_by` copies right now, because doing
        so would push some quota below its floor.

        `reduce_by` matters: the re-normalize pass below can delete a 2-of
        outright, which removes TWO copies. Protecting only at `count <= floor`
        would let a quota sitting one copy above its floor be knocked two
        copies below it."""
        out = set()
        for _label, members, floor in _quotas:
            if _qcount(gg, members) - reduce_by < floor:
                out |= members
        return out

    for _label, _members, _floor in _quotas:
        if len(_members) * MAX_COPIES < _floor and not _FORCE_WARNED.get("_q:" + _label):
            print(f"WARNING: pool has {len(_members)} ink-legal {_label}(s) "
                  f"({len(_members) * MAX_COPIES} copies) < floor {_floor}; "
                  f"that quota cannot be met and will be left short.")
            _FORCE_WARNED["_q:" + _label] = True
        qguard = 0
        while _qcount(g, _members) < _floor and qguard < 10000:
            qguard += 1
            # Prefer topping up a member the genome ALREADY chose. Sprinkling a
            # fresh random member on every repair would make these slots pure
            # noise that selection never gets to act on -- the floor would be
            # met, but WHICH songs / 1-drops would be re-rolled every generation.
            present = [n for n in sorted(_members) if 0 < g.get(n, 0) < MAX_COPIES]
            if present:
                pick = rng.choice(present)
                g[pick] = g[pick] + 1
            else:
                newc = [n for n in sorted(_members) if n not in g]
                if not newc:
                    break
                # Enter at 2, never 1: the no-singleton fixup below resolves a
                # 1-of by promoting OR dropping, and a drop would undo the quota.
                g[rng.choice(newc)] = 2

    # --- size: add or remove copies until exactly 60 ---
    size = genome_size(g)
    guard = 0
    while size < DECK_SIZE and guard < 10000:
        guard += 1
        cands = [c.name for c in legal if g.get(c.name, 0) < MAX_COPIES]
        if not cands:
            break
        pick = rng.choice(cands)
        g[pick] = g.get(pick, 0) + 1
        size += 1
    while size > DECK_SIZE and guard < 20000:
        guard += 1
        # trim, but never below a forced floor or a quota floor
        _prot = _protected(g, 1)
        cands = [n for n, k in g.items()
                 if k > forced.get(n, 0) and n not in _prot]
        if not cands:
            # Nothing unprotected left. A legal 60 is a hard game rule; a quota
            # is only a search bias, so the quota yields. _format_report marks
            # the result SHORT so this never passes unnoticed.
            cands = [n for n, k in g.items() if k > forced.get(n, 0)]
        if not cands:
            break
        pick = rng.choice(cands)
        g[pick] -= 1
        if g[pick] == 0:
            del g[pick]
        size -= 1

    # --- no singletons: every included card must be 2..4 copies ---
    # Resolve each 1-of by either promoting it to 2 or dropping it, chosen so the
    # deck can still reach exactly 60. Iterate because each change shifts size.
    fixup_guard = 0
    while fixup_guard < 20000:
        fixup_guard += 1
        singles = [n for n, k in g.items() if k == 1 and forced.get(n, 0) <= 1]
        # forced cards with floor 1 could legitimately be a 1-of; if you want
        # forced floors to also obey no-singleton, raise their floor to 2 in FORCE.
        if not singles:
            break
        size = genome_size(g)
        n = rng.choice(singles)
        # A quota member at its floor must be PROMOTED even when the deck is
        # oversize; dropping it is what would break the floor. The re-normalize
        # pass below absorbs the extra copy from somewhere unprotected.
        if size <= DECK_SIZE or n in _protected(g, 1):
            g[n] = 2            # promote (adds 1 to size)
        else:
            del g[n]            # drop (removes 1 from size)

    # After singleton fixup the size may be off by a little; re-normalize WITHOUT
    # creating new singletons: only add to cards already >=1 (making them >=2),
    # and only trim cards that are >=3 (so they stay >=2), or drop a 2 to 0.
    # ONE convergent loop, not grow-then-trim. Both directions move in steps of
    # 2 sometimes (adding a new card as a 2-of; deleting a 2-of outright), so
    # either can overshoot -- e.g. trimming 61 by deleting a 2-of lands on 59.
    # Sequential grow-then-trim has no way back from that overshoot and returns
    # a 59-card deck. Looping until size == DECK_SIZE self-corrects instead.
    size = genome_size(g)
    guard = 0
    while size != DECK_SIZE and guard < 60000:
        guard += 1
        if size < DECK_SIZE:
            # prefer topping up an existing card (keeps it >=2); else add a NEW
            # card as a 2-of to avoid creating a transient singleton.
            present = [n for n in g if g[n] < MAX_COPIES]
            if present:
                pick = rng.choice(present)
                g[pick] += 1
                size += 1
            else:
                newc = [c.name for c in legal if c.name not in g]
                if not newc:
                    break
                g[rng.choice(newc)] = 2
                size += 2
        else:
            # Trim a card that stays >=2, else drop a 2-of entirely (never leave
            # a 1). Strict priority: EVERY unprotected option is exhausted
            # before a quota is allowed to yield. Falling back to a protected
            # 3-of merely because no unprotected 3-of exists would breach the
            # floor while unprotected 2-ofs were still sitting there.
            # deleting a 2-of removes TWO copies, so test the quota at depth 2.
            _prot1 = _protected(g, 1)
            _prot2 = _protected(g, 2)
            threes = [n for n, k in g.items()
                      if k >= 3 and k - 1 >= forced.get(n, 0)]
            twos = [n for n, k in g.items()
                    if k == 2 and forced.get(n, 0) < 2]
            free3 = [n for n in threes if n not in _prot1]
            free2 = [n for n in twos if n not in _prot2]
            if free3:
                pick = rng.choice(free3); g[pick] -= 1; size -= 1
            elif free2:
                pick = rng.choice(free2); del g[pick]; size -= 2
            elif threes:                 # quota yields; a legal 60 is a hard rule
                pick = rng.choice(threes); g[pick] -= 1; size -= 1
            elif twos:
                pick = rng.choice(twos); del g[pick]; size -= 2
            else:
                break
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
                        workers, seed0, pool_obj=None, label=None):
    """Return list of win rates (0..1), one per genome, averaged over the field.
    Every genome faces the SAME seeds, so comparisons between genomes are paired.

    If `label` is set, emit a throttled PROGRESS line as games complete (used by
    the verification phase to show games-done / total). Unlabeled calls (the GA
    generations) stay quiet here and keep their per-generation logging."""
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

    # Progress logging (only when labeled). Log ~1% increments, at least every
    # game if the batch is tiny, so a long verification phase shows a live count
    # and ETA without flooding the log.
    _pg_total = len(tasks)
    _pg_done = 0
    _pg_start = time.time()
    _pg_every = max(1, _pg_total // 100)

    def _pg_tick():
        nonlocal _pg_done
        _pg_done += 1
        if label and (_pg_done % _pg_every == 0 or _pg_done == _pg_total):
            _el = time.time() - _pg_start
            _rate = _pg_done / _el if _el > 0 else 0.0
            _eta = (_pg_total - _pg_done) / _rate / 60 if _rate > 0 else 0.0
            _log("PROGRESS", event="games", phase=label,
                 done=_pg_done, of=_pg_total,
                 pct=f"{100*_pg_done/_pg_total:.1f}",
                 games_per_s=f"{_rate:.2f}", eta_min=f"{_eta:.0f}")

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
            _pg_tick()
    elif pool_obj is not None:
        # Reuse the caller's long-lived Pool (see evolve()).
        for gid, won in pool_obj.imap_unordered(_fit_task, tasks, chunksize=_chunk):
            wins[gid] += won
            played[gid] += 1
            _pg_tick()
    else:
        with Pool(workers, initializer=_init_fit, initargs=initargs) as pool_:
            for gid, won in pool_.imap_unordered(_fit_task, tasks, chunksize=_chunk):
                wins[gid] += won
                played[gid] += 1
                _pg_tick()
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
    and introduce/remove a card entirely.

    Swaps are type-aware in ONE direction: a song leaving must be replaced by a
    song, but a non-song may be replaced by anything, songs included. The
    asymmetry is the point -- it lets the song count drift UP under selection
    while MIN_SONGS stops it drifting down. Without it, a song swapped out for
    an arbitrary card gets replaced by an arbitrary DIFFERENT song by the quota
    fill in repair(), so the song slots are re-randomized every generation and
    selection never accumulates pressure on which songs the deck actually wants."""
    g = dict(g)
    by_name = {c.name: c for c in pool}
    legal = [c.name for c in pool]
    songs = [c.name for c in pool if c.is_song]
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
                _c_out = by_name.get(out)
                src = songs if (_c_out is not None and _c_out.is_song) else legal
                cands = [n for n in src if n not in g]
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
# Structured progress logging
# =====================================================================
def _log(prefix, **fields):
    """One structured, flushed log line: `PREFIX  t=...  k=v  k=v ...`.

    Timestamped, greppable, and flushed so it survives redirection to a file
    or `nohup` (unflushed stdout buffers into silent bursts). Reconstruct a
    run's trace with `grep '^PROGRESS' log`.
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    body = "  ".join(f"{k}={v}" for k, v in fields.items())
    print(f"{prefix}  t={ts}  {body}", flush=True)


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
#    workers = workers or os.cpu_count() or 1
    workers = workers or 6# or 1

    cap = pool_capacity(pool)
    print(f"Pool: {len(pool)} unique legal cards (capacity {cap} >= {DECK_SIZE} required)")
    if cap < DECK_SIZE:
        print("ERROR: pool cannot make a legal 60-card deck.")
        sys.exit(1)

    # Over-constraint guard. If the mandatory minimums exceed the deck size,
    # repair()'s trim loops run out of unprotected candidates and return an
    # ILLEGAL genome of >60 cards, silently. Fail loudly instead.
    # Conservative: FORCE and the quotas may overlap (a forced song counts
    # toward MIN_SONGS), so this can reject a configuration that would in fact
    # just barely fit. Loosen it only if you hit that case for real.
    _mandatory = sum(FORCE.values()) + MIN_SONGS + MIN_ONE_DROPS
    if _mandatory > DECK_SIZE:
        print(f"ERROR: mandatory minimums total {_mandatory} > {DECK_SIZE} "
              f"(FORCE={sum(FORCE.values())}, MIN_SONGS={MIN_SONGS}, "
              f"MIN_ONE_DROPS={MIN_ONE_DROPS}). Lower one of them.")
        sys.exit(1)

    # Report what the quotas can actually draw on, before burning hours on a
    # run whose floors can never be met.
    for _label, _members, _floor in _quota_specs(pool):
        print(f"Quota {_label}: floor {_floor}, pool has {len(_members)} "
              f"ink-legal name(s) = {len(_members) * MAX_COPIES} copies available")

    _log("CONFIG",
         pool_cards=len(pool),
         field_decks=len(field_paths),
         field=";".join(os.path.basename(p) for p in field_paths),
         inks=",".join(ink_pair) if ink_pair else "auto",
         pop=pop_size, generations=generations, elite=elite,
         games=games, policy=pol, fit_iters=iters,
         verify_top=verify_top, verify_games=verify_games,
         verify_iters=verify_iters, workers=workers, seed=seed,
         min_songs=MIN_SONGS, min_one_drops=MIN_ONE_DROPS,
         forced=";".join(f"{k}:{v}" for k, v in sorted(FORCE.items())) or "none",
         games_per_gen=pop_size * len(field_paths) * games)
    _run_start = time.time()

    # --- resume? ---
    start_gen = 0
    pop = None
    cached_final = None   # {"sig":..., "fits":[...], "best_g":{...}, "best_f":float}
    # Signature of the settings that determine the final-scoring result. A cached
    # final block is only reused when this matches, so changing the field, games,
    # policy or fit-iters correctly forces a recompute (mirrors gauntlet's guard).
    # The deck CONSTRAINTS are part of this too: a cached score computed for
    # unconstrained decks says nothing about a run that now floors songs and
    # 1-drops, and reusing it would hand back a champion scored under the old
    # rules without a word of warning.
    final_sig = "|".join(str(x) for x in (
        os.path.abspath(pool_path),
        ";".join(os.path.abspath(p) for p in field_paths),
        games, pol, iters, seed,
        MIN_SONGS, MIN_ONE_DROPS,
        ";".join(f"{k}:{v}" for k, v in sorted(FORCE.items()))))
    if checkpoint and os.path.exists(checkpoint):
        try:
            with open(checkpoint) as f:
                st = json.load(f)
            if st.get("pool_path") == os.path.abspath(pool_path):
                # Re-repair on load. Elites are carried forward each generation
                # as dict(pop[i]) WITHOUT a repair call, so a genome checkpointed
                # before these constraints existed would otherwise survive every
                # generation untouched while only its offspring obeyed the floors.
                pop = [repair(dict(g), pool, rng, ink_pair)
                       for g in st["population"]]
                start_gen = st["generation"]
                print(f"Resuming deckbuild from generation {start_gen} "
                      f"(population re-repaired against current constraints)")
                # Reuse a previously-computed final-scoring pass only when it was
                # produced for THIS population and THESE settings.
                fin = st.get("final")
                if (fin and fin.get("sig") == final_sig
                        and len(fin.get("fits", [])) == len(pop)):
                    cached_final = fin
                    print("  (found matching cached final scores; the "
                          "final-scoring pass will be skipped)")
        except Exception:
            pop = None
            cached_final = None

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
            _gen_start = time.time()
            _games_this_gen = len(pop) * len(field_paths) * games
            _log("PROGRESS", event="gen_start", gen=gen, of=generations,
                 pop=len(pop), games_this_gen=_games_this_gen)

            fits = evaluate_population(pop, db_path, field_paths, games, pol, iters,
                                       workers, seed0=seed + gen * 1000,
                                       pool_obj=_pool)
            _elapsed = time.time() - _gen_start

            order = sorted(range(len(pop)), key=lambda i: -fits[i])
            if fits[order[0]] > best_f:
                best_f, best_g = fits[order[0]], dict(pop[order[0]])
            mean_f = sum(fits) / len(fits)

            # convergence signals
            _spread = statistics.pstdev(fits) if len(fits) > 1 else 0.0
            _distinct_genomes = len({tuple(sorted(g.items())) for g in pop})
            _distinct_cards = len({n for g in pop for n in g})
            # self-correcting ETA from this generation's real duration
            _gens_left = generations - gen - 1
            _eta_min = (_gens_left * _elapsed) / 60.0
            _gps = _games_this_gen / _elapsed if _elapsed > 0 else 0.0

            _log("PROGRESS", event="gen_end", gen=gen, of=generations,
                 elapsed_s=f"{_elapsed:.0f}", games_per_s=f"{_gps:.2f}",
                 best=f"{100*fits[order[0]]:.1f}", mean=f"{100*mean_f:.1f}",
                 best_so_far=f"{100*best_f:.1f}", spread=f"{100*_spread:.1f}",
                 distinct_genomes=_distinct_genomes, distinct_cards=_distinct_cards,
                 eta_min=f"{_eta_min:.0f}")

            print(f"  gen {gen:2d}/{generations}  best {100*fits[order[0]]:.0f}%  "
                  f"mean {100*mean_f:.0f}%  (pop {len(pop)}, {games} games/deck)",
                  flush=True)

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
                _log("PROGRESS", event="checkpoint", gen_saved=gen + 1,
                     path=checkpoint)

        # --- final scoring of the last population, then MCTS verification ---
        # On a verify-only resume (no generations ran this invocation) with a
        # matching cached block, skip the ~1-generation final-scoring pass and
        # reuse the stored scores. Any evolution this run makes the cache invalid.
        reuse_final = (cached_final is not None and start_gen >= generations)
        if reuse_final:
            fits = list(cached_final["fits"])
            if cached_final.get("best_g") is not None:
                best_g = cached_final["best_g"]
                best_f = cached_final.get("best_f", best_f)
            _log("PROGRESS", event="final_scoring", status="skipped_cached")
            print("Skipping final-scoring pass; using cached scores from checkpoint.")
        else:
            fits = evaluate_population(pop, db_path, field_paths, games, pol, iters,
                                       workers, seed0=seed + 999999, pool_obj=_pool)
            # Persist the final-scoring result so a later verify-only resume can
            # skip this pass. Best-effort: a write failure never fails the run.
            if checkpoint and os.path.exists(checkpoint):
                try:
                    with open(checkpoint) as f:
                        _st = json.load(f)
                    _st["final"] = {"sig": final_sig, "fits": fits,
                                    "best_g": best_g, "best_f": best_f}
                    tmp = checkpoint + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(_st, f)
                    os.replace(tmp, checkpoint)
                    _log("PROGRESS", event="final_scoring", status="computed_cached")
                except Exception:
                    pass
        order = sorted(range(len(pop)), key=lambda i: -fits[i])
        finalists = [pop[i] for i in order[:verify_top]]
        if best_g is not None and not any(g == best_g for g in finalists):
            finalists.append(best_g)

        print(f"\nVerifying {len(finalists)} finalist(s) with MCTS "
              f"({verify_games} games/opponent, {verify_iters} iters)... this is the slow part.")
        _verify_start = time.time()
        _log("VERIFY", event="start", finalists=len(finalists),
             games_per_opp=verify_games, iters=verify_iters,
             total_games=len(finalists) * len(field_paths) * verify_games)
        vfits = evaluate_population(finalists, db_path, field_paths, verify_games,
                                    "mcts", verify_iters, workers, seed0=seed + 7,
                                    pool_obj=_pool, label="verify")
        _log("VERIFY", event="done",
             elapsed_min=f"{(time.time() - _verify_start) / 60:.1f}",
             run_total_min=f"{(time.time() - _run_start) / 60:.1f}")
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
    # Composition vs. the configured floors: a quota that ended up SHORT means
    # the pool could not supply it (repair() warns once); a quota sitting exactly
    # at its floor means fitness is pushing against the constraint.
    _songs = sum(k for n, k in champ.items() if by_name[n].is_song)
    _ones = sum(k for n, k in champ.items()
                if by_name[n].cost == 1
                and by_name[n].card_type in ("Character", "Location", "Item"))
    L.append(f"  songs: {_songs} (floor {MIN_SONGS})"
             f"{'  << SHORT' if _songs < MIN_SONGS else ''}")
    L.append(f"  1-cost permanents: {_ones} (floor {MIN_ONE_DROPS})"
             f"{'  << SHORT' if _ones < MIN_ONE_DROPS else ''}")
    _curve = Counter(by_name[n].cost for n, k in champ.items() for _ in range(k))
    L.append("  curve: " + "  ".join(f"{c}:{_curve[c]}"
                                     for c in sorted(_curve)))
    L.append("\nCAVEATS")
    L.append("  * The GA searched with a WEAK policy; greedy undervalues decks that")
    L.append("    need clever sequencing. The MCTS pass re-ranks finalists but cannot")
    L.append("    recover a strong deck the search never explored.")
    L.append("  * This is a local optimum tuned to THIS field. A deck that beats these")
    L.append("    opponents may fold to a different one. Re-run with a wider field.")
    L.append("  * Confirm the champion with `gauntlet` before trusting it, and use")
    L.append("    `analyze` to check its curve and loss patterns.")
    return "\n".join(L)
