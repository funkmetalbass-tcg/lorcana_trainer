# Lorcana Two-Deck Trainer

A rules engine + ISMCTS AI scoped to two decks:
**Deck A** (Set 12 Locations, Ruby/Steel) vs **Deck B** (Set 13 Toys, Amber/Emerald).
Pure Python 3, no dependencies.

## Quick start

```bash
python3 run.py manifest                 # audit card implementations + assumptions
python3 run.py sim --games 200          # greedy-vs-greedy baseline (~1s)
python3 run.py sim --games 20 --a mcts --b greedy --iters 150
python3 run.py watch --a mcts --b mcts --iters 150 --seed 3   # verbose game log
python3 run.py play --human A --iters 400                     # THE TRAINER
python3 run.py analyze --study A --a mcts --b mcts --iters 150 --games 200 --out report.txt
python3 run.py coverage                  # per-card implementation source; flags unimplemented
python3 run.py gauntlet --field deckB1.txt deckB2.txt deckB3.txt --games 100 --iters 100
```

All commands accept `--db`, `--deck-a`, `--deck-b` to point at other files.

## Finding universally-good cuts (`gauntlet`)

`gauntlet` tests candidate cuts from Deck A against a whole FIELD of opponent
decks at once, to find cards that are worth cutting against *everything* rather
than just one matchup. It runs in parallel across CPU cores.

```bash
python3 run.py gauntlet \
    --field deckB1.txt deckB2.txt deckB3.txt \
    --candidates auto \        # auto (analyze drag-flags) | all | --candidates-file list.txt
    --games 100 --iters 100 \
    --a mcts --b mcts \
    --workers 8 \              # default: CPU count; 1 = serial
    --out gauntlet.txt
```

How it works and why it's trustworthy:

- **Paired seeds.** For each candidate, the baseline deck and the one-card-cut
  deck play the *same* game seeds against each opponent, so luck cancels and the
  per-seed win difference isolates that single card's effect. This detects real
  effects with far fewer games than independent runs.
- **Mean + worst-case.** Each candidate is scored by its average win-rate delta
  across the field *and* its worst single matchup. A `<- universal cut` flag
  means cutting helps on average and doesn't badly hurt any one matchup;
  `(matchup-dependent)` means it helps overall but tanks a specific pairing
  (a sideboard consideration, not a maindeck cut).
- **Deterministic & parallel.** Results are identical whether run on 1 or 8
  workers (verified); seeds travel with each task, so scheduling never changes
  outcomes. Use `--workers` to trade cores for wall-clock time.
- **Crash-resumable.** Pass `--checkpoint run.jsonl` and each finished game is
  appended to that file as it completes. If the machine crashes, re-run the
  exact same command with the same `--checkpoint` path and it skips everything
  already done and finishes the rest — the resumed result is identical to an
  uninterrupted run (paired seeds make this exact, not approximate). The file
  records a signature of the run parameters, so it refuses to resume into a
  different set of decks/params rather than mixing incompatible results. Use
  `--no-resume` to ignore an existing checkpoint and start fresh.
- **Live partial results.** On every progress tick the current standings are
  written to your `--out` path (or `--partial-out`), atomically, marked
  `*** PARTIAL RESULTS -- N/M games complete ***` and annotated with per-cell
  sample sizes (`n=`). If a run stalls or you kill it, the file on disk already
  holds usable, honestly-labelled standings. Tune the frequency with
  `--progress-every N` (default 1 = every game).
- **Stall diagnosis.** The terminal heartbeat shows elapsed time, games/min, and
  an ETA, so a genuine hang (rate collapsing) is distinguishable from slow work.
  Work is dispatched one game at a time (`chunksize=1`) to avoid a ragged tail
  where a single worker grinds a whole batch while others idle — which used to
  look exactly like a freeze near the end of a long run.

The `--candidates` cut is one copy removed and one existing card duplicated
(size stays 60). Like `analyze --suggest`, this answers *"am I better off with
one fewer of this card, across the field?"* — a cut test. To test real
additions, edit a decklist and re-run. Cuts are evaluated one at a time, so
interactions between two simultaneous cuts aren't captured; confirm your top
one or two candidates with a larger `--games` run before committing.

## Swapping decks / generalization status

The engine is being generalized to the whole master JSON (see
`GENERALIZATION_ROADMAP.md`). Current state:

- **Keywords are generic pool-wide.** Any card whose printed text is just
  keywords (Bodyguard, Evasive, Rush, Ward, Reckless, Support, Resist +N,
  Challenger +N, Singer N, Shift N, Sing Together N) plays correctly with no
  per-card code — you can drop such cards into a decklist and they just work.
- **Named abilities** are still either hand-coded (the two sample decks) or
  expressed as data in `lorcana/abilities_manual.json` (the schema system).
- **`run.py coverage`** shows, per card in your decks, how each is implemented
  (python / schema-manual / schema-auto / keywords / vanilla) and loudly flags
  any card with unhandled ability text as `UNIMPLEMENTED`. Run it whenever you
  swap in a new deck: if nothing is flagged, simulations account for every card;
  if something is flagged, that card's special text is being ignored until you
  add a schema entry or Python logic.
- **Extending coverage (offline parser).** `tools/parse_abilities.py` reads the
  master JSON and emits `lorcana/abilities_auto.json` by matching ability text
  against tight templates. It never runs during a game -- it produces data you
  review. To grow the implemented pool: run it with `--stats` (and
  `--show-unimpl 40` to sample gaps), add a template + a matching effect handler
  in `schema.py`, regenerate with `--out lorcana/abilities_auto.json`, then run
  `test_phase3.py`. Hand-authored entries in `abilities_manual.json` always
  override the parser and are never regenerated.

## Diagnosing a losing deck (`analyze`)

`analyze` runs many games with the studied deck piloted by the AI and reports
what to change. **Use `--a mcts --b mcts`** — under `greedy` the location
payoffs get inked away and look falsely weak, because greedy doesn't understand
the location plan. Expect ~16s/game at 150 iters, so 200 games ≈ 55 min; pass
`--out report.txt` so a long run is saved, and watch the live progress on stderr.

It prints three things:

1. **Per-card contribution** — for each card: how often it's drawn (`seen%`),
   played when drawn (`play%`), left dead in hand (`dead%`), spent as ink
   (`ink%`), and the win-rate `delta` between games where it was played vs never
   drawn. The table is sorted worst-delta-first, so **cut candidates float to
   the top**. A card that's mostly inked (`ink%` high, `play%` low) is doing
   filler duty; a strongly negative delta means games went worse when it showed up.
2. **Tempo & curve** — average ink per turn, the turn each cost bucket first
   hits the board, and your opening-hand inkable distribution (ink-screw risk).
3. **Loss patterns** — fast (raced) vs grind (out-of-gas) losses, average lore
   gap, and how often losses coincide with location flooding or a low-ink
   opening — with a plain-language READ suggesting the direction to adjust.

Add `--suggest` to A/B test trimming each flagged card (it re-simulates with one
copy removed and reports the win-rate change). To test real *additions*, edit
the decklist and re-run `analyze`.

**Deltas are correlational, not causal** — a card can ride along in good draws.
Treat the table as a prioritized list of hypotheses, then confirm with `--suggest`
or a decklist edit.

## The trainer (`play`)

You pilot one deck against an MCTS opponent. Each turn you get a numbered
action menu. Commands:

- **number** — take that action
- **hint** — run MCTS from your seat; prints moves ranked by visit count with
  estimated win probability (chess-engine style). Compare its top line to your
  instinct *before* looking. `--iters 800`+ gives stronger, slower advice.
- **board / log / quit**

Training loop that works well: play your real-life deck seat, ask `hint` on
every non-obvious decision, and note the positions where your pick isn't in
the top 2. Those are your leaks.

## "You may" effects are real choices

Zootopia's draw-then-discard and Sleepy Hollow's banish-for-lore are **not**
auto-resolved — they appear as separate menu entries the AI evaluates
independently:

- Moving to Zootopia offers *"[Zootopia: draw then discard]"* vs *"[no draw]"*.
- Questing at Sleepy Hollow offers *"[banish Sleepy Hollow: +2 lore & Evasive]"*
  vs *"[keep Sleepy Hollow]"*.

The search discounts wins by how long they take, so it will bank the Sleepy
Hollow banish for lethal but hold it otherwise.

## Strength notes

- `random` and `greedy` are shakedown baselines. At 150 iterations MCTS beats
  greedy from **both** seats — ~62% piloting the location deck (which greedy
  loses ~86% of the time) and ~100% piloting the toys deck — by finding the
  location gameplan (Touch the Sky chains, Illuminary lore stacking, Carl/
  Pocahontas value, Sleepy Hollow cash-ins) that the heuristic misses.
- Win-probability numbers from `hint` are estimates from greedy rollouts;
  trust the *ranking* more than the absolute values.

## Please review

Run `python3 run.py manifest` and check each card's listed interpretation plus
the ASSUMPTIONS section. "Chosen character" triggers (Jessie YODEL, Mickey
SECRET PATH, Elinor, CHEAP SHOT, ENDLESS WINTER) are still auto-targeted by
documented heuristics rather than exposed as AI decisions; Carl's "up to 1
other" move and Pocahontas's returned location are also auto-selected. If any
reading is wrong, that's the fastest way to catch it before trusting win rates.

Then validate against your real games: replay key turns in `play` mode and see
whether `hint` agrees with what strong opponents did.

## Layout

```
run.py                  entry point
lorcana/cards.py        card DB + decklist parser
lorcana/engine.py       state, turn flow, combat, action generation
lorcana/abilities.py    all per-card logic + ASSUMPTIONS list
lorcana/policies.py     mulligan / random / greedy (rollout policy)
lorcana/mcts.py         information-set MCTS with determinization + depth discount
lorcana/analyze.py      deck diagnostics: per-card, tempo/curve, loss patterns
lorcana/gauntlet.py     parallel multi-opponent cut testing (multiprocessing)
lorcana/keywords.py     Phase 1: printed-keyword parser (generic, pool-wide)
lorcana/schema.py       Phase 2: data-driven ability dispatcher
lorcana/abilities_manual.json   hand-authored schema ability entries (override auto)
lorcana/abilities_auto.json     parser-generated entries (reviewable; regenerable)
tools/parse_abilities.py        Phase 3: offline template parser (never runs in-match)
lorcana/cli.py          commands
test_mechanics.py       59 forced-scenario mechanic tests
test_phase3.py          parser + schema-auto pipeline tests
GENERALIZATION_ROADMAP.md  plan for covering the full card pool
```

