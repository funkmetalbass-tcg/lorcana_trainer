"""Gauntlet: test candidate cuts from Deck A against a FIELD of opponent decks,
using paired seeds and multiprocessing, to find cuts that are universally good
(help on average and don't tank any single matchup).

Statistical design
------------------
For each (candidate cut, opponent) we play the SAME set of game seeds with two
configurations -- the baseline deck and the deck with one copy of the candidate
removed (and one copy of an existing card duplicated to keep 60). Because both
configs see identical shuffles and opponent behavior per seed, the luck largely
cancels: the per-seed win difference isolates the effect of the single card.

We aggregate the cut-minus-baseline win-rate delta per opponent, then summarize
each candidate by:
  mean_delta   -- average improvement across the field (higher = better cut)
  worst_delta  -- the single worst matchup (guards against matchup-dependent cuts)
A universally-good cut has mean_delta > 0 AND worst_delta not badly negative.

Parallelism
-----------
Work is a flat list of (config_id, opponent_id, seed) tasks. A multiprocessing
pool plays them; each worker loads decks once per task-chunk. Seeds are carried
in the task so results are deterministic regardless of worker scheduling.
"""
import os
import random
from collections import defaultdict
from multiprocessing import Pool

from .cards import CardDB, parse_decklist
from .engine import Game
from .policies import default_mulligan
from .analyze import make_policy


# ---------------------------------------------------------------------
# Lightweight game runner (win/loss only -- no per-card tracking overhead)
# ---------------------------------------------------------------------
def _play(deckU, deckO, polU, polO, seed, study_on_seat0):
    if study_on_seat0:
        g = Game(deckU, deckO, seed=seed); seat = 0
    else:
        g = Game(deckO, deckU, seed=seed); seat = 1
    g.start(mulligan_fn=lambda game, p: default_mulligan(game, p))
    rng = random.Random(seed)
    while g.winner is None and g.turn < 120:
        pol = polU if g.active == seat else polO
        g.apply(pol(g, rng))
    if g.winner is None and g.players[0].lore != g.players[1].lore:
        g.winner = 0 if g.players[0].lore > g.players[1].lore else 1
    return int(g.winner == seat)


# ---------------------------------------------------------------------
# Deck construction helpers
# ---------------------------------------------------------------------
def _apply_cut(deck, cut_name):
    """Return a new decklist with one copy of cut_name removed and one LEGAL
    extra copy of another in-deck card added (keeps the deck at 60 so tempo and
    consistency aren't perturbed by simply running 59 cards).

    The backfill must never create a 5th copy of any card -- delegated to
    analyze.pick_filler, which also breaks ties deterministically."""
    from .analyze import apply_cut
    out, _note = apply_cut(deck, cut_name)
    return out


# Worker globals (populated once per process via initializer to avoid
# re-parsing the big JSON for every task).
_W = {}


def _init_worker(db_path, deckA_path, field_paths, a_pol, b_pol, iters):
    db = CardDB(db_path)
    deckA, _, _ = parse_decklist(deckA_path, db)
    field = []
    for fp in field_paths:
        d, _, _ = parse_decklist(fp, db)
        field.append(d)
    _W["deckA"] = deckA
    _W["field"] = field
    _W["a_pol_name"] = a_pol
    _W["b_pol_name"] = b_pol
    _W["iters"] = iters


def _run_task(task):
    """task = (config, opp_idx, seed, study_seat0)
    config is None (baseline) or a card name to cut.
    Returns (config, opp_idx, won)."""
    config, opp_idx, seed, study_seat0 = task
    deckA = _W["deckA"]
    deckU = deckA if config is None else _apply_cut(deckA, config)
    deckO = _W["field"][opp_idx]
    # fresh policies per task, seeded off the game seed for determinism
    polU = make_policy(_W["a_pol_name"], _W["iters"], seed=seed * 2 + 1)
    polO = make_policy(_W["b_pol_name"], _W["iters"], seed=seed * 2 + 2)
    won = _play(deckU, deckO, polU, polO, seed, study_seat0)
    return (config, opp_idx, won)


def _run_task_ck(task):
    """Like _run_task but also returns the task key, so out-of-order parallel
    results can be tied back to their originating task for checkpointing."""
    config, opp_idx, won = _run_task(task)
    return (_task_key(task), config, opp_idx, won)


def _task_key(task):
    """Stable identity for a task, independent of worker scheduling."""
    config, opp_idx, seed, study_seat0 = task
    cfg = config if config is not None else "\x00BASELINE"
    return f"{cfg}\t{opp_idx}\t{seed}\t{int(study_seat0)}"


def _run_signature(db_path, deckA_path, field_paths, candidates, games, iters,
                   a_pol, b_pol, seed0):
    """Fingerprint of the run parameters. A checkpoint may only be resumed by a
    run with an identical signature, so incompatible results are never mixed."""
    import hashlib, json as _json
    payload = _json.dumps({
        "db": os.path.abspath(db_path),
        "deckA": os.path.abspath(deckA_path),
        "field": [os.path.abspath(p) for p in field_paths],
        "candidates": list(candidates),
        "games": games, "iters": iters,
        "a_pol": a_pol, "b_pol": b_pol, "seed0": seed0,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _load_checkpoint(path, signature):
    """Return {task_key: (config, opp_idx, won)} of completed tasks, or {} if the
    file is absent or belongs to a different run."""
    if not path or not os.path.exists(path):
        return {}
    import json as _json, sys
    done = {}
    with open(path) as f:
        header = f.readline().strip()
        try:
            meta = _json.loads(header)
        except Exception:
            return {}
        if meta.get("signature") != signature:
            sys.stderr.write(
                "\nWARNING: checkpoint exists but its run signature does not match "
                "this run (different decks/params). Ignoring it; delete it or use a "
                "fresh --checkpoint path.\n")
            return {}
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = _json.loads(line)      # [task_key, config, opp_idx, won]
                done[rec[0]] = (rec[1], rec[2], rec[3])
            except Exception:
                continue                     # torn final line from a crash: skip
    return done


def _open_checkpoint(path, signature, is_new):
    """Open the checkpoint for appending; write the header if it's new."""
    if not path:
        return None
    import json as _json
    f = open(path, "w" if is_new else "a", buffering=1)  # line-buffered
    if is_new:
        f.write(_json.dumps({"signature": signature,
                             "format": "gauntlet-checkpoint-v1"}) + "\n")
        f.flush()
    return f


def _partial_results(wins, played, candidates, n_opp):
    """Build a results dict from whatever has completed so far."""
    def wr(config, opp_idx):
        n = played[(config, opp_idx)]
        return (wins[(config, opp_idx)] / n) if n else 0.0
    baseline = [wr(None, o) for o in range(n_opp)]
    cut = {c: [wr(c, o) for o in range(n_opp)] for c in candidates}
    return {"baseline": baseline, "cut": cut, "n_opp": n_opp,
            "wins": dict(wins), "played": dict(played)}


def _write_partial(path, wins, played, candidates, n_opp, field_labels,
                   done, total):
    """Atomically write the current standings to `path`. Written on every
    progress tick so a stalled or killed run still leaves usable results."""
    if not path:
        return
    R = _partial_results(wins, played, candidates, n_opp)
    try:
        body = format_gauntlet(R, field_labels, partial=(done, total))
    except Exception as e:                      # never let reporting kill a run
        body = f"(partial report unavailable: {e})"
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(body + "\n")
        os.replace(tmp, path)                   # atomic on POSIX & Windows
    except Exception:
        pass                                    # a failed partial write is not fatal


def run_gauntlet(db_path, deckA_path, field_paths, candidates, games, iters,
                 a_pol="mcts", b_pol="mcts", workers=None, seed0=0,
                 progress=None, checkpoint=None, resume=True,
                 partial_out=None, field_labels=None):
    """Returns a results dict:
        baseline[opp_idx]   -> win rate (0..1)
        cut[config][opp_idx]-> win rate (0..1)
    plus the raw win counts for significance if wanted."""
    n_opp = len(field_paths)
    workers = workers or os.cpu_count() or 1

    # Build task list. Baseline (config=None) shares seeds with every cut so
    # the comparison is paired. Alternate who's on the play via the seed parity.
    configs = [None] + list(candidates)
    tasks = []
    for config in configs:
        for opp_idx in range(n_opp):
            for gi in range(games):
                seed = seed0 + gi
                study_seat0 = (gi % 2 == 0)
                tasks.append((config, opp_idx, seed, study_seat0))

    wins = defaultdict(int)     # (config, opp_idx) -> win count
    played = defaultdict(int)

    # --- checkpoint / resume ---
    signature = _run_signature(db_path, deckA_path, field_paths, candidates,
                               games, iters, a_pol, b_pol, seed0)
    completed = _load_checkpoint(checkpoint, signature) if resume else {}
    # Fold already-completed results into the tallies and drop those tasks.
    if completed:
        for tk, (config, opp_idx, won) in completed.items():
            cfg = None if config in (None, "\x00BASELINE") else config
            wins[(cfg, opp_idx)] += won
            played[(cfg, opp_idx)] += 1
        before = len(tasks)
        tasks = [t for t in tasks if _task_key(t) not in completed]
        import sys
        sys.stderr.write(f"\nResuming from checkpoint: {before - len(tasks)}/"
                         f"{before} games already done; {len(tasks)} remaining.\n")

    ckpt_file = _open_checkpoint(checkpoint, signature,
                                 is_new=not bool(completed))

    import json as _json

    def _record(task, config, opp_idx, won):
        wins[(config, opp_idx)] += won
        played[(config, opp_idx)] += 1
        if ckpt_file is not None:
            cfg = config if config is not None else "\x00BASELINE"
            ckpt_file.write(_json.dumps([_task_key(task), cfg, opp_idx, won]) + "\n")

    initargs = (db_path, deckA_path, field_paths, a_pol, b_pol, iters)
    done = 0
    total = len(tasks)
    already = sum(played.values())          # games folded in from checkpoint
    labels = field_labels or [f"opp{i+1}" for i in range(n_opp)]

    def _tick():
        """Update the terminal counter AND flush a partial report to disk."""
        overall_done = already + done
        overall_total = already + total
        _emit_progress(overall_done, overall_total)
        if ckpt_file is not None:
            ckpt_file.flush()
        _write_partial(partial_out, wins, played, candidates, n_opp, labels,
                       overall_done, overall_total)

    # map task_key -> task so imap_unordered results can be tied back for logging
    task_by_key = {_task_key(t): t for t in tasks}
    # progress: default to EVERY game so a genuine stall is distinguishable from
    # slow-but-working. (A coarse interval makes the tail of a run look frozen.)
    step = progress if progress and progress > 0 else 1
    if workers == 1:
        _init_worker(*initargs)
        for t in tasks:
            config, opp_idx, won = _run_task(t)
            _record(t, config, opp_idx, won)
            done += 1
            if done % step == 0 or done == total:
                _tick()
    else:
        # chunksize=1: hand out one game at a time. With multi-second MCTS games
        # the dispatch overhead is negligible, and it removes the ragged tail
        # where one worker grinds a whole chunk while the others idle (which
        # looks exactly like a stall near the end of a long run).
        with Pool(workers, initializer=_init_worker, initargs=initargs) as pool:
            for tk, config, opp_idx, won in pool.imap_unordered(
                    _run_task_ck, tasks, chunksize=1):
                _record(task_by_key[tk], config, opp_idx, won)
                done += 1
                if done % step == 0 or done == total:
                    _tick()
    _tick()

    if ckpt_file is not None:
        ckpt_file.flush()
        ckpt_file.close()

    def wr(config, opp_idx):
        n = played[(config, opp_idx)]
        return (wins[(config, opp_idx)] / n) if n else 0.0

    baseline = [wr(None, o) for o in range(n_opp)]
    cut = {}
    for config in candidates:
        cut[config] = [wr(config, o) for o in range(n_opp)]
    return {"baseline": baseline, "cut": cut, "n_opp": n_opp,
            "games": games, "wins": dict(wins), "played": dict(played)}


_PROG_T0 = None


def _emit_progress(done, total):
    """Terminal heartbeat. Shows elapsed time and games/min so a genuine stall
    (rate collapsing, timestamp frozen) is distinguishable from slow progress."""
    import sys, time
    global _PROG_T0
    now = time.time()
    if _PROG_T0 is None:
        _PROG_T0 = now
    elapsed = max(1e-9, now - _PROG_T0)
    rate = done / elapsed * 60.0
    remain = (total - done) / (done / elapsed) if done else float("inf")
    eta = f"{remain/60:.0f}m" if remain != float("inf") else "?"
    sys.stderr.write(
        f"\r  gauntlet {done}/{total} ({100.0*done/total:.0f}%)  "
        f"{elapsed/60:.1f}m elapsed  {rate:.1f} games/min  ETA ~{eta}   ")
    sys.stderr.flush()


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------
def format_gauntlet(R, field_labels, candidate_order=None, partial=None):
    base = R["baseline"]
    n = R["n_opp"]
    played = R.get("played", {})
    lines = []
    lines.append("\n" + "=" * 78)
    lines.append("GAUNTLET: candidate cuts vs. a field of opponent decks")
    if partial:
        done, total = partial
        pct = (100.0 * done / total) if total else 0.0
        lines.append(f"*** PARTIAL RESULTS -- {done}/{total} games ({pct:.0f}%) complete ***")
        lines.append("*** Numbers will shift as remaining games finish. ***")
    lines.append("=" * 78)
    base_mean = sum(base) / n if n else 0.0
    lines.append("\nBaseline Deck A win rate per opponent:")
    for i, lab in enumerate(field_labels):
        ng = played.get((None, i), 0)
        lines.append(f"    vs {lab:24s} {100*base[i]:5.0f}%   (n={ng})")
    lines.append(f"    {'FIELD MEAN':27s} {100*base_mean:5.0f}%")

    # per-candidate deltas
    rows = []
    for config, wrs in R["cut"].items():
        deltas = [wrs[i] - base[i] for i in range(n)]
        mean_d = sum(deltas) / n if n else 0.0
        worst_d = min(deltas) if deltas else 0.0
        ns = [played.get((config, i), 0) for i in range(n)]
        rows.append((config, mean_d, worst_d, deltas, ns))
    # good universal cut = high mean, and worst-case not badly negative
    rows.sort(key=lambda r: (-(r[1]), -r[2]))
    if candidate_order == "worst":
        rows.sort(key=lambda r: r[2])

    lines.append("\nCANDIDATE CUTS (delta = win rate WITHOUT the card minus baseline)")
    lines.append("  Positive mean = cutting helps on average. worst = weakest matchup.")
    lines.append("  n = games completed for that candidate (per opponent).")
    lines.append(f"\n  {'cut this card':34s} {'mean':>6s} {'worst':>6s}   per-opponent deltas")
    lines.append("  " + "-" * 74)
    for config, mean_d, worst_d, deltas, ns in rows:
        per = " ".join(f"{100*d:+4.0f}" for d in deltas)
        nstr = "/".join(str(x) for x in ns)
        flag = ""
        if mean_d > 0.02 and worst_d > -0.05:
            flag = "  <- universal cut"
        elif mean_d > 0.02 and worst_d <= -0.05:
            flag = "  (matchup-dependent)"
        lines.append(f"  {config[:34]:34s} {100*mean_d:+5.0f}% {100*worst_d:+5.0f}%   "
                     f"{per}  n={nstr}{flag}")

    lines.append("\n  Columns after 'worst' are per-opponent, in this order:")
    lines.append("    " + " | ".join(f"{i+1}:{lab}" for i, lab in enumerate(field_labels)))
    lines.append("\nNOTE: a 'universal cut' helps on average and doesn't badly hurt any single\n"
                 "matchup. Confirm the top candidates with a larger --games run before\n"
                 "committing, and remember cuts are tested one at a time (interactions\n"
                 "between two cuts are not captured).")
    return "\n".join(lines)
