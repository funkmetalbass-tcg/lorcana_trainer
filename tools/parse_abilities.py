#!/usr/bin/env python3
"""Phase 3: OFFLINE ability template parser.

Reads the master card JSON, matches each card's ability text against a small
set of TIGHT templates, and emits a reviewable `abilities_auto.json`. This
never runs at match time -- its output is data a human reads and corrects.

Design rules (non-negotiable, because a wrong parse silently poisons win rates):
  1. Conservative. A template only matches if it consumes the ENTIRE residual
     ability sentence(s). Partial matches are rejected -> card stays unimplemented.
  2. Transparent. Every emitted entry carries the source text and a confidence.
     Every un-parsed card is emitted as {"impl": "unimplemented", "text": ...}
     so it shows up in `run.py coverage` as a visible gap.
  3. Non-destructive. Cards present in abilities_manual.json are skipped
     entirely (hand authoring always wins and is never overwritten).
  4. Idempotent. Re-running reproduces the same file; safe to regenerate.

Usage:
    python3 tools/parse_abilities.py master_legal_cardlist.json \
            --out lorcana/abilities_auto.json
    python3 tools/parse_abilities.py master_legal_cardlist.json --stats
"""
import argparse, json, os, re, sys

# Make the package importable when run from repo root or tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lorcana.keywords import clean_text, residual_prose  # noqa: E402


# ---------------------------------------------------------------------
# Text normalization: strip reminder parens and [NAMED ABILITY] markers,
# collapse whitespace. We parse the "core" prose that the keyword layer
# didn't already consume.
# ---------------------------------------------------------------------
def core_prose(desc):
    t = clean_text(desc)
    t = re.sub(r"\([^)]*\)", "", t)          # reminder text
    t = re.sub(r"\[[^\]]*\]", "", t)         # [NAMED ABILITY] labels
    t = t.replace("{}", " ")                 # ink/lore symbol notation
    # Bare ALLCAPS ability names (no brackets) -- 70 cards print them this way.
    t = re.sub(r"(?m)(?:^|(?<=[.\n]))\s*[A-Z][A-Z0-9'\u2019 !,&.-]{3,40}?(?=\s*(?:\[|\u2014|\u2013|-\s|$))", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------------
# Templates. Each is (compiled_regex, builder). The regex must match the
# WHOLE core prose (via fullmatch). builder(match) -> ability dict.
# Keep these TIGHT. When in doubt, don't add it -- leave the card
# unimplemented rather than risk a wrong parse.
# ---------------------------------------------------------------------
_TEMPLATES = []


def template(pattern, confidence="high"):
    rx = re.compile(pattern, re.IGNORECASE)

    def deco(fn):
        _TEMPLATES.append((rx, fn, confidence))
        return fn
    return deco


def _src(desc):
    return clean_text(desc).strip()


# --- on_play -----------------------------------------------------------
@template(r"When you play this character, draw a card\.?")
def _t(m):
    return {"trigger": "on_play", "effect": {"type": "draw", "amount": 1}}


@template(r"When you play this character, (?:you may )?gain (\d+) lore\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "gain_lore", "amount": int(m.group(1))}}


@template(r"When you play this character, chosen character gets \+(\d+) Strength this turn\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "stat_mod", "stat": "str", "amount": int(m.group(1)),
                       "target": "chosen_character", "duration": "eot"}}


@template(r"When you play this character, chosen character gets \+(\d+) Lore this turn\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "stat_mod", "stat": "lore", "amount": int(m.group(1)),
                       "target": "chosen_character", "duration": "eot"}}


@template(r"When you play this character, chosen opposing character gets \-(\d+) Strength this turn\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "stat_mod", "stat": "str", "amount": -int(m.group(1)),
                       "target": "chosen_opposing", "duration": "eot"}}


@template(r"When you play this character, deal (\d+) damage to chosen character\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "deal_damage", "amount": int(m.group(1)),
                       "target": "chosen_opposing"}}


@template(r"When you play this character, each opponent loses (\d+) lore\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "opponent_lose_lore", "amount": int(m.group(1))}}


@template(r"When you play this character, each opponent chooses and discards a card\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "opponent_discard", "amount": 1}}


@template(r"When you play this character, chosen opposing character gets \-(\d+) Strength until the start of your next turn\.?")
def _t(m):
    return {"trigger": "on_play",
            "effect": {"type": "stat_mod", "stat": "str", "amount": -int(m.group(1)),
                       "target": "chosen_opposing", "duration": "until_your_next"}}


@template(r"Your locations gain Resist \+(\d+)\.?")
def _t(m):
    return {"trigger": "static",
            "effect": {"type": "static_location_resist", "amount": int(m.group(1))}}


# --- on_quest ----------------------------------------------------------
@template(r"Whenever this character quests, (?:you may )?gain (\d+) lore\.?")
def _t(m):
    return {"trigger": "on_quest",
            "effect": {"type": "gain_lore", "amount": int(m.group(1))}}


@template(r"Whenever this character quests, (?:you may )?draw a card\.?")
def _t(m):
    return {"trigger": "on_quest", "effect": {"type": "draw", "amount": 1}}


@template(r"Whenever this character quests, chosen opposing character gets \-(\d+) Strength this turn\.?")
def _t(m):
    return {"trigger": "on_quest",
            "effect": {"type": "stat_mod", "stat": "str", "amount": -int(m.group(1)),
                       "target": "chosen_opposing", "duration": "eot"}}


# --- static conditional lore/strength (WHILE ...) ----------------------
@template(r"While you have (?:a|an) character named ([\w '\-\.]+?) in play, this character gets \+(\d+) Lore\.?")
def _t(m):
    return {"trigger": "static",
            "condition": {"type": "you_have_named", "name": m.group(1).strip()},
            "effect": {"type": "static_self_lore", "amount": int(m.group(2))}}


# --- cost reduction on play (self is a character; discount next thing) --
@template(r"When you play this character, you pay (\d+) Ink less for the next (character|location|action|item) you play this turn\.?")
def _t(m):
    filt = {"character": "character", "location": "location",
            "action": "action", "item": "item"}[m.group(2).lower()]
    return {"trigger": "on_play",
            "effect": {"type": "cost_reduce", "amount": int(m.group(1)), "filter": filt}}


# ---------------------------------------------------------------------
# CLAUSE templates: match a SINGLE sentence. Used only when the whole-text
# templates fail. Composition rule stays conservative -- EVERY sentence must
# match a clause or the card stays unimplemented.
#
# These carry trigger on_play because abilities.py routes action cards through
# schema.dispatch_play (abilities.py:1536), so an action's resolution and a
# character's enters-play trigger share one dispatch point.
# ---------------------------------------------------------------------
_CLAUSES = []


def clause(pattern, confidence="high"):
    rx = re.compile(pattern, re.IGNORECASE)

    def deco(fn):
        _CLAUSES.append((rx, fn, confidence))
        return fn
    return deco


@clause(r"Deal (\d+) damage to chosen character\.?")
def _c(m):
    return {"type": "deal_damage", "amount": int(m.group(1)),
            "target": "chosen_opposing"}


@clause(r"Chosen character gets \+(\d+) Strength this turn\.?")
def _c(m):
    return {"type": "stat_mod", "stat": "str", "amount": int(m.group(1)),
            "target": "chosen_character", "duration": "eot"}


@clause(r"Chosen character gets \+(\d+) Lore this turn\.?")
def _c(m):
    return {"type": "stat_mod", "stat": "lore", "amount": int(m.group(1)),
            "target": "chosen_character", "duration": "eot"}


@clause(r"Chosen opposing character gets \-(\d+) Strength this turn\.?")
def _c(m):
    return {"type": "stat_mod", "stat": "str", "amount": -int(m.group(1)),
            "target": "chosen_opposing", "duration": "eot"}


@clause(r"Draw a card\.?")
def _c(m):
    return {"type": "draw", "amount": 1}


@clause(r"Draw (\d+) cards\.?")
def _c(m):
    return {"type": "draw", "amount": int(m.group(1))}


@clause(r"Gain (\d+) lore\.?")
def _c(m):
    return {"type": "gain_lore", "amount": int(m.group(1))}


@clause(r"Each opponent loses (\d+) lore\.?")
def _c(m):
    return {"type": "opponent_lose_lore", "amount": int(m.group(1))}


@clause(r"Each opponent chooses and discards a card\.?")
def _c(m):
    return {"type": "opponent_discard", "amount": 1}


_SENT = re.compile(r"(?<=\.)\s+")


def parse_by_clauses(prose):
    """Split into sentences; require EVERY sentence to match a clause.
    Returns list of effect dicts, or None."""
    sents = [x.strip() for x in _SENT.split(prose) if x.strip()]
    if len(sents) < 2:
        return None            # single-sentence cards are the whole-text path
    effects = []
    for sent in sents:
        hit = None
        for rx, builder, conf in _CLAUSES:
            m = rx.fullmatch(sent)
            if m:
                hit = builder(m)
                break
        if hit is None:
            return None        # one unrecognized sentence rejects the card
        effects.append(hit)
    return effects


# ---------------------------------------------------------------------
# Parser core
# ---------------------------------------------------------------------
def parse_card(name, raw):
    """Return a list of ability entries, or a single unimplemented marker.
    None means: nothing to implement (vanilla or fully keyword-covered)."""
    desc = raw.get("Description", "") or ""
    if not clean_text(desc).strip():
        return None                       # vanilla
    if not residual_prose(desc):
        return None                       # keyword-only; engine handles it

    prose = core_prose(desc)
    for rx, builder, conf in _TEMPLATES:
        m = rx.fullmatch(prose)
        if m:
            entry = builder(m)
            entry["confidence"] = conf
            entry["source"] = _src(desc)
            return [entry]

    # Single-sentence bare-imperative (action cards): try clauses directly.
    for rx, builder, conf in _CLAUSES:
        m = rx.fullmatch(prose)
        if m:
            return [{"trigger": "on_play", "effect": builder(m),
                     "confidence": conf, "source": _src(desc)}]

    # Multi-sentence composition.
    effects = parse_by_clauses(prose)
    if effects:
        return [{"trigger": "on_play", "effect": e, "confidence": "medium",
                 "source": _src(desc)} for e in effects]

    # nothing matched -> visible gap
    return [{"impl": "unimplemented", "text": _src(desc)}]


def build(json_path, manual_path):
    with open(json_path) as f:
        db = json.load(f)
    manual = {}
    if manual_path and os.path.exists(manual_path):
        with open(manual_path) as f:
            manual = json.load(f)
    # Cards already implemented in Python (abilities.HAND_IMPLEMENTED) must not
    # also get an auto entry, or their effects would double-apply.
    hand = set()
    try:
        from lorcana.abilities import HAND_IMPLEMENTED
        hand = set(HAND_IMPLEMENTED)
    except Exception:
        pass

    out = {}
    stats = {"vanilla_or_kw": 0, "parsed": 0, "unimplemented": 0,
             "skipped_manual": 0, "skipped_python": 0, "by_template": {}}
    for name, raw in db.items():
        if not isinstance(raw, dict) or "CardType" not in raw:
            continue
        if name in manual:
            stats["skipped_manual"] += 1
            continue
        if name in hand:
            stats["skipped_python"] += 1
            continue
        result = parse_card(name, raw)
        if result is None:
            stats["vanilla_or_kw"] += 1
            continue
        if result[0].get("impl") == "unimplemented":
            stats["unimplemented"] += 1
        else:
            stats["parsed"] += 1
            trg = result[0].get("trigger", "?")
            eff = result[0].get("effect", {}).get("type", "?")
            key = f"{trg}/{eff}"
            stats["by_template"][key] = stats["by_template"].get(key, 0) + 1
        out[name] = result
    return out, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--out", default=None,
                    help="write abilities_auto.json here (default: print stats only)")
    ap.add_argument("--manual", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lorcana", "abilities_manual.json"))
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--show-unimpl", type=int, default=0,
                    help="print N sample unimplemented ability texts")
    args = ap.parse_args()

    out, stats = build(args.json_path, args.manual)
    total = sum(v for k, v in stats.items() if isinstance(v, int))
    print("Parse summary:")
    print(f"  vanilla / keyword-only (no entry): {stats['vanilla_or_kw']}")
    print(f"  skipped (hand-authored in manual):  {stats['skipped_manual']}")
    print(f"  skipped (implemented in python):    {stats['skipped_python']}")
    print(f"  auto-parsed:                        {stats['parsed']}")
    print(f"  unimplemented (visible gaps):       {stats['unimplemented']}")
    if stats["by_template"]:
        print("  by template:")
        for k, v in sorted(stats["by_template"].items(), key=lambda x: -x[1]):
            print(f"     {v:4d}  {k}")

    if args.show_unimpl:
        shown = 0
        for name, ents in out.items():
            if ents and ents[0].get("impl") == "unimplemented":
                print(f"    [{name}] {ents[0]['text'][:90]}")
                shown += 1
                if shown >= args.show_unimpl:
                    break

    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
        print(f"\nWrote {len(out)} card entries to {args.out}")
        print("Review it, then promote good entries into abilities_manual.json "
              "(which overrides auto and is never regenerated).")


if __name__ == "__main__":
    main()
