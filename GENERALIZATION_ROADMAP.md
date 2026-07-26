# Generalizing the Trainer to the Full Card Pool

## The core realization

1,029 cards, but only ~845 have non-trivial text, and those cluster into a
small number of **templates**. ~18% (vanilla + keyword-only) already need zero
custom code. A modest structured schema + template parser can likely cover
50-70% more, leaving a manageable hand-authored tail. The goal is a system
where an unimplemented card is *visibly* unimplemented, never silently wrong.

## Architecture: three sources, tried in order

For each card the ability loader resolves abilities from the first source that
has an entry:

1. **Hand-authored** (`abilities_manual.json` / Python registry) — for the
   gnarly cards. Highest priority, always wins.
2. **Auto-parsed** (`abilities_auto.json`) — emitted offline by the parser,
   reviewed by a human, checked into the repo. Never parsed at runtime.
3. **Keywords + vanilla** — parsed directly from stats/text at load time
   (Bodyguard, Evasive, Resist +N, Shift N, ...). Always applied on top.

A card with no entry in (1) or (2) is a vanilla body with its keywords. If its
text has un-handled prose, the loader flags it `unimplemented` so decks
containing it warn loudly (exactly like the old placeholder behavior).

## The ability schema (data, not code)

```json
{
  "trigger": "on_play",
  "condition": {"you_have_named": "Woody"},
  "effect": {"type": "gain_lore", "amount": 1, "target": "self"}
}
```

- **triggers**: on_play, on_quest, on_challenge, on_banish, start_turn,
  end_turn, while (static/conditional), activated (exert), on_play_location, ...
- **conditions**: you_have_named, you_have_classification, at_location,
  opponent_has, self_damaged, count_comparison, ...
- **effects**: gain_lore, draw, discard, deal_damage, banish, return_to_hand,
  stat_mod (str/will/lore/resist, target, duration), cost_reduce, exert,
  ready, mill, search, play_free, ...
- **targets**: self, chosen_opposing_character, chosen_character, all_yours,
  all_opposing, this_location_occupants, ...

The engine grows ONE generic dispatcher per trigger that walks the ability
list, checks conditions, and applies effects. The giant `if name == ...`
chains in abilities.py get replaced by data + a few dozen effect handlers.

## Rollout phases (each shippable on its own)

### Phase 1 — Keyword layer  [DONE]
Printed keywords (Bodyguard, Evasive, Rush, Ward, Reckless, Resist, Challenger,
Support, Singer, Shift, Sing Together) are parsed once at load into
`Card.keywords` by `lorcana/keywords.py`; the engine reads them generically with
no per-card code. Ward/Reckless/Support/Singer mechanics were added to the
engine. The parser distinguishes PRINTED keywords (pure keyword lines before the
first [NAMED ABILITY]) from prose ("gains Evasive this turn", dynamic "Resist +1
per location"), so those are correctly not treated as printed.
Verified: a deck of 7 never-before-seen pool cards loads, reports full keyword
coverage via `run.py coverage`, and plays crash-free.

### Phase 2 — Ability schema + dispatcher  [SKELETON DONE]
`lorcana/schema.py` + `abilities_manual.json`: data-driven abilities (trigger +
optional condition + effect), manual>auto merge loader, condition/effect
handlers, and dispatch_play/dispatch_quest hooks wired into abilities.py. Three
cards migrated from Python to data as proof (Aurora, Jessie's conditional draw,
Elsa - Concerned Sister); all 59 tests pass. Implemented: triggers on_play,
on_quest; conditions your_other_classification_count, you_have_named,
opponent_ahead; effects draw, gain_lore, cost_reduce, stat_mod, deal_damage.
GROW as templates demand.

### Phase 3 — Offline template parser -> reviewed abilities_auto.json  [DONE]
`tools/parse_abilities.py` reads the master JSON, matches each card's residual
ability prose against TIGHT fullmatch templates, and writes a reviewable
`lorcana/abilities_auto.json`. It is offline-only (never runs at match time),
conservative (a template must consume the entire prose or the card is left
`unimplemented`), non-destructive (skips cards in abilities_manual.json AND
cards in abilities.HAND_IMPLEMENTED, so no double-apply), and deterministic.
Each parsed entry carries its source text + confidence; every unmatched card
becomes an `{"impl": "unimplemented", "text": ...}` marker that shows up in
`run.py coverage`. The schema runtime loads auto beneath manual (manual wins),
and `entries_for` refuses to run schema for any python-implemented card as a
belt-and-suspenders guard. ~16 cards auto-parse today from a dozen templates;
grow coverage by adding templates (each covers all sibling cards using that
phrasing) and promoting good entries into abilities_manual.json.

Workflow to extend:
  1. `python3 tools/parse_abilities.py master_legal_cardlist.json --stats`
     (see what parses; `--show-unimpl 40` to sample gaps)
  2. add a template in parse_abilities.py + matching effect handler in schema.py
  3. `... --out lorcana/abilities_auto.json` to regenerate
  4. `python3 test_phase3.py && python3 test_mechanics.py` to verify
  5. review new entries; promote any needing tweaks into abilities_manual.json

### Phase 4 — Coverage reporting + deck legality gate  [PARTIAL]
`run.py coverage` exists and labels each deck card python / schema-manual /
schema-auto / keywords / vanilla, flagging UNIMPLEMENTED gaps. Still TODO:
optionally make `sim`/`analyze` refuse to run when a deck contains
unimplemented cards (currently they warn via coverage only).
Write `tools/parse_abilities.py`. For each card, try ordered regex templates:
  "While you have a character named X in play, this character gets +N {stat}."
  "When you play this character, gain N lore."
  "When you play this character, draw a card."
  "Whenever this character quests, chosen character gets -N Strength this turn."
  "You pay N Ink less to play this character if you have {cond}."
  ...
Emit one JSON entry per matched card with a `confidence` and the source text.
Everything unmatched -> `{"impl": "unimplemented", "text": "..."}`.
Human reviews the diff, fixes/promotes entries. Re-run freely; manual entries
are never overwritten.
Test: a golden set — hand-verify ~40 parsed cards across templates; lock them
in as regression fixtures.

### Phase 4 — Coverage reporting + deck legality gate
`python3 run.py coverage --deck somedeck.txt` prints, per card, which source
implemented it and flags any `unimplemented`. Simulations refuse (or loudly
warn) when a deck contains unimplemented cards, so results are never quietly
based on missing rules.

### Phase 5 — Long tail, on demand
You don't implement 845 cards up front. When you want to test a new deck, run
coverage; implement only the handful it flags, as hand-authored entries or new
parser templates (which then cover siblings for free). The pool fills in
lazily, driven by the decks you actually play.

## What stays hard (accept and isolate)
Some cards need bespoke code no schema will capture cleanly (complex modal
choices, unusual replacement effects, multi-step search-and-arrange). Keep the
hand-authored escape hatch: a card entry may say `{"impl": "python:funcname"}`
and run a real function. Aim to keep this set small, but don't contort the
schema to avoid it.

## Decision points to settle before Phase 2
- **Choices vs heuristics.** The current code auto-resolves many "chosen"
  targets. In a general system, decide per-effect whether to expose the choice
  to MCTS (accurate, more branching) or keep a documented heuristic (faster).
  Recommendation: expose targeting choices for effects that clearly matter
  (removal, buffs on your turn); heuristic for minor/forced ones.
- **Runtime vs offline parsing.** Strong recommendation: offline only. Parsed
  output is data you can read and correct; runtime parsing hides errors.
- **Where the schema lives.** JSON is reviewable and language-agnostic; a
  Python DSL is more expressive but couples data to code. Recommend JSON for
  parsed entries, with a Python escape hatch for the hard tail.
