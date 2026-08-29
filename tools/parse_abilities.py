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
from lorcana.keywords import (clean_text, residual_prose,  # noqa: E402
                              parse_printed_keywords,
                              _PATTERNS as _KW_PATTERNS)


# ---------------------------------------------------------------------
# Text normalization: strip reminder parens and [NAMED ABILITY] markers,
# collapse whitespace. We parse the "core" prose that the keyword layer
# didn't already consume.
# ---------------------------------------------------------------------
def core_prose(desc):
    t = clean_text(desc)
    t = re.sub(r"\([^)]*\)", "", t)          # reminder text
    t = re.sub(r"\[[^\]]*\]", "", t)         # [NAMED ABILITY] labels
    # Some cards print the ability name bare, with no brackets at all
    # (Miriam Mendelsohn: "I GOT 'EM! When you play this character, ...").
    # Require a following capitalised word so ordinary prose is untouched.
    t = re.sub(r"^\s*[A-Z][A-Z0-9'\u2019 &.,-]{2,40}[!?.]\s+(?=[A-Z])", "", t)
    t = t.replace("\u2019", "'")             # curly apostrophe
    t = t.replace("{}", " ")                 # ink/lore symbol notation
    # Printed keywords are already handled by the keyword layer; leaving them
    # in the prose blocks every template on cards that carry both a keyword
    # and an ability (Shift + a triggered effect, Sing Together + an effect).
    for kw in parse_printed_keywords(desc):
        t = _KW_PATTERNS[kw].sub(" ", t, count=1)
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


# --- Phase 4 clauses -------------------------------------------------
@clause(r"Deal (\d+) damage to chosen character\. This damage can't be reduced by Resist\.?")
def _c(m):
    return {"type": "deal_damage", "amount": int(m.group(1)),
            "target": "chosen_opposing", "ignore_resist": True}


@clause(r"Deal (\d+) damage to chosen damaged character\.?")
def _c(m):
    return {"type": "deal_damage", "amount": int(m.group(1)),
            "filter": {"damaged": True}}


@clause(r"Chosen opposing character gets \-(\d+) (Strength|Lore) until the start of your next turn\.?")
def _c(m):
    return {"type": "stat_mod",
            "stat": "str" if m.group(2).lower() == "strength" else "lore",
            "amount": -int(m.group(1)), "target": "chosen_opposing",
            "duration": "until_your_next"}


@clause(r"You pay (\d+) Ink less for the next (character|action|item|location) you play this turn\.?")
def _c(m):
    return {"type": "cost_reduce", "amount": int(m.group(1)),
            "filter": m.group(2).lower()}


@clause(r"Banish all opposing damaged characters\.?")
def _c(m):
    return {"type": "banish_all", "side": "opposing",
            "filter": {"damaged": True}}


@clause(r"Banish all opposing characters\.?")
def _c(m):
    return {"type": "banish_all", "side": "opposing"}


@clause(r"Play a character from your discard for free\.?")
def _c(m):
    return {"type": "play_from_discard", "filter": {"card_type": "character"}}


@clause(r"Play a character with cost up to (\d+) more than the banished character for free\.?")
def _c(m):
    return {"type": "play_from_hand_free", "max_cost_delta": int(m.group(1)),
            "filter": {"card_type": "character"}}


@clause(r"[Rr]eturn chosen opposing character with (\d+) Strength or less to their player's hand\.?")
def _c(m):
    return {"type": "return_to_hand", "side": "opposing",
            "filter": {"max_strength": int(m.group(1))}}


@clause(r"[Rr]eturn chosen opposing character to their player's hand\.?")
def _c(m):
    return {"type": "return_to_hand", "side": "opposing"}


# Dig-N is one composite effect spanning three sentences, so it is matched
# as a whole-text unit rather than sentence by sentence.
_DIG = re.compile(
    r"look at the top (\d+) cards of your deck\. "
    r"You may reveal an? (character|item|action) card"
    r"(?: with cost (\d+) or less)? and put it into your hand\. "
    r"Put the rest on the bottom of your deck(?: in any order)?\.?",
    re.IGNORECASE)


def _dig_effect(m):
    filt = {"card_type": m.group(2).lower()}
    if m.group(3):
        filt["max_cost"] = int(m.group(3))
    return {"type": "look_at_top", "count": int(m.group(1)),
            "destination": "hand", "filter": filt}



# --- Phase 5 clauses -------------------------------------------------
@clause(r"Put (\d+) damage counters? on chosen character\.?")
def _c(m):
    # Damage counters are not "damage dealt", so Resist does not apply.
    return {"type": "deal_damage", "amount": int(m.group(1)),
            "target": "chosen_opposing", "ignore_resist": True}


@clause(r"Each opposing damaged character gains (Reckless|Evasive|Ward|Rush) until the start of your next turn\.?")
def _c(m):
    return {"type": "mass_grant_keyword", "side": "opposing",
            "filter": {"damaged": True}, "keyword": m.group(1).lower(),
            "duration": "until_your_next"}


@clause(r"Banish chosen location\.?")
def _c(m):
    return {"type": "banish_location"}


@clause(r"Up to (\d+) chosen characters can't quest until the start of your next turn\.?")
def _c(m):
    return {"type": "quest_lock", "count": int(m.group(1)),
            "duration": "until_your_next"}


@clause(r"Chosen character can't quest until the start of your next turn\.?")
def _c(m):
    return {"type": "quest_lock", "count": 1, "duration": "until_your_next"}


@clause(r"You pay (\d+) Ink less for the next action or item you play this turn\.?")
def _c(m):
    return {"type": "cost_reduce", "amount": int(m.group(1)),
            "filter": "action_or_item"}


@clause(r"Chosen opponent chooses 3 of their characters and returns one of those cards "
        r"to their hand, puts one on the bottom of their deck, and puts one on the top "
        r"of their deck\.?")
def _c(m):
    return {"type": "opponent_scatter"}


# Reveal-top-1. Same shape as dig-N but with an OR filter and a named
# exception, so it gets its own pattern rather than more optional groups.
_REVEAL = re.compile(
    r"Reveal the top card of your deck\. If it's an? (non-character|character) card"
    r"(?: or an? (?:character )?card named ([A-Za-z' -]+))?, put it into your hand\. "
    r"Otherwise, put it on the bottom of your deck\.?",
    re.IGNORECASE)


def _reveal_effect(m):
    base = {"card_type": m.group(1).lower().replace("-", "_")
            if m.group(1).lower() == "non-character" else m.group(1).lower()}
    if m.group(2):
        filt = {"any_of": [base, {"card_type": "character",
                                  "name": m.group(2).strip()}]}
    else:
        filt = base
    return {"type": "look_at_top", "count": 1, "destination": "hand",
            "filter": filt}



# --- Phase 6 clauses -------------------------------------------------
@clause(r"Move up to (\d+) damage from chosen character of yours to this character\. "
        r"Then, if this character has (\d+) or more damage, move all damage from this "
        r"character to chosen opposing character\.?")
def _c(m):
    return {"type": "move_damage", "amount": int(m.group(1)),
            "dump_at": int(m.group(2))}


@clause(r"[Yy]ou may reveal the top card of your deck\. If you do, you may play it\. "
        r"Otherwise, put it into your discard\.?")
def _c(m):
    return {"type": "reveal_and_play"}


@clause(r"[Rr]eturn chosen character, item, or location with cost (\d+) or less "
        r"to their player's hand\.?")
def _c(m):
    return {"type": "return_to_hand", "side": "opposing",
            "zones": ["character", "item", "location"],
            "filter": {"max_cost": int(m.group(1))}}



# --- Phase 7 clauses -------------------------------------------------
@clause(r"[Cc]hosen character gains (Evasive|Ward|Rush|Reckless|Support) "
        r"until the start of your next turn\.?")
def _c(m):
    # Protective grants go to the quester, matching the best_quester note in
    # _resolve_target rather than the strongest attacker.
    return {"type": "grant_keyword", "keyword": m.group(1).lower(),
            "target": "best_quester", "duration": "until_your_next"}


@clause(r"[Pp]ut this card on the top of your deck\.?")
def _c(m):
    return {"type": "self_to_deck_top"}



# --- Phase 8 clauses -------------------------------------------------
@clause(r"[Ee]ach player draws a card\.?")
def _c(m):
    return {"type": "each_player_draw", "amount": 1}


@clause(r"[Ee]ach player draws (\d+) cards\.?")
def _c(m):
    return {"type": "each_player_draw", "amount": int(m.group(1))}


def match_clause(text):
    """Effect dict for a single clause, or None."""
    text = text.strip()
    m = _DIG.fullmatch(text)
    if m:
        return _dig_effect(m)
    m = _REVEAL.fullmatch(text)
    if m:
        return _reveal_effect(m)
    for rx, builder, conf in _CLAUSES:
        m = rx.fullmatch(text)
        if m:
            return builder(m)
    return None


# ---------------------------------------------------------------------
# Activated abilities: "NAME [Exert], 1 Ink, Banish this item - EFFECT"
#
# Parsed off the cleaned text rather than core_prose, because core_prose
# deletes the [Exert] token and the ability name -- which is where the cost
# lives. The separator is normalized first: across the pool 63 cards use an
# em dash, 8 an en dash and 12 a plain hyphen.
# ---------------------------------------------------------------------
_ACT_HEAD = re.compile(
    r"^\s*(?:[A-Z][A-Z0-9'\u2019 !,&.?-]{2,45}?\s+)?"       # optional ALLCAPS name
    r"((?:\[Exert\]|\{\}|\d+\s*Ink|Banish this [a-z]+)"      # first cost token
    r"(?:\s*,\s*[^\u2014]{1,45}?)*)"                          # further cost tokens
    r"\s*\u2014\s*(.+)$")                                    # separator + effect


def _parse_cost(text):
    cost = {}
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if re.fullmatch(r"\[Exert\]|\{\}", tok):
            cost["exert"] = True
            continue
        m = re.fullmatch(r"(\d+)\s*(?:Ink|\{\})", tok, re.IGNORECASE)
        if m:
            cost["ink"] = int(m.group(1))
            continue
        if re.fullmatch(r"Banish this (item|character|location)", tok, re.IGNORECASE):
            cost["banish_self"] = True
            continue
        if re.fullmatch(r"Banish chosen character of yours", tok, re.IGNORECASE):
            cost["banish_own_char"] = True
            continue
        m = re.fullmatch(r"Discard (?:a card|(\d+) cards)", tok, re.IGNORECASE)
        if m:
            cost["discard"] = int(m.group(1) or 1)
            continue
        return None            # unrecognized cost token -> reject the card
    return cost or None



_FREE_IF = re.compile(
    r"If a character named ([A-Za-z' .-]+) was banished this turn, "
    r"you may play this (?:item|character|action) for free\.?",
    re.IGNORECASE)


_LABEL = re.compile(r"^\s*(?:\[[^\]]*\]|[A-Z][A-Z0-9' !,&.-]{2,45}?(?=\s+[A-Z][a-z]))\s*")


def parse_static_line(line):
    """Static/replacement lines that are not activated abilities.
    Returns a single entry, a list of entries, or None."""
    line = _LABEL.sub("", line.strip())
    ss = parse_static_self(line)
    if ss:
        return ss
    m = _SELF_DISCOUNT.fullmatch(line.strip())
    if m:
        return {"trigger": "static",
                "condition": {"type": "first_turn_on_the_draw"},
                "effect": {"type": "play_cost_reduction",
                           "amount": int(m.group(1))}}
    m = _FREE_IF.fullmatch(line.strip())
    if m:
        return {"trigger": "static",
                "condition": {"type": "named_banished_this_turn",
                              "name": m.group(1).strip()},
                "effect": {"type": "play_free_if"}}
    return None


_COND_PREFIX = re.compile(r"^If you've played (\d+) or more cards this turn,\s*",
                          re.IGNORECASE)

_SELF_DISCOUNT = re.compile(
    r"If this is your first turn and you're not the first player, "
    r"you pay (\d+) Ink less to play this (?:character|item|action|location)\.?",
    re.IGNORECASE)


# The Strength symbol arrives as "{}" in this card export. Confirmed by
# Grandma Wu, whose Challenger reminder text reads "gets +2 {}" -- Challenger
# is unambiguously a Strength bonus.
_STAT_SYMBOL = "str"

_STATIC_SELF = re.compile(
    r"While (?P<cond>.+?), (?:this character|she|he|they) gets \+(?P<amt>\d+) "
    r"(?P<stat>\{\}|Strength|Lore|Willpower)"
    r"(?: and gains (?P<kw>Evasive|Ward|Reckless|Rush|Support))?\.?",
    re.IGNORECASE)

_STATIC_CONDS = [
    (re.compile(r"all cards in your inkwell are exerted", re.I),
     {"type": "inkwell_all_exerted"}),
    (re.compile(r"there's a card under this character", re.I),
     {"type": "has_card_under"}),
]


def _stat_name(tok):
    tok = tok.strip().lower()
    if tok == "{}":
        return _STAT_SYMBOL
    return {"strength": "str", "lore": "lore", "willpower": "will"}[tok]


def parse_static_self(line):
    """'While <condition>, this character gets +N <stat> [and gains KW].'"""
    m = _STATIC_SELF.fullmatch(line.strip())
    if not m:
        return None
    cond = None
    for rx, c in _STATIC_CONDS:
        if rx.fullmatch(m.group("cond").strip()):
            cond = c
            break
    if cond is None:
        return None                 # unrecognized condition -> leave the gap
    stat = _stat_name(m.group("stat"))
    out = [{"trigger": "static", "condition": cond,
            "effect": {"type": "static_self_stat", "stat": stat,
                       "amount": int(m.group("amt"))}}]
    if m.group("kw"):
        out.append({"trigger": "static", "condition": cond,
                    "effect": {"type": "static_self_keyword",
                               "keyword": m.group("kw").lower()}})
    return out


# ---------------------------------------------------------------------
# Shared line normalization for the activated and static parsers.
#
# core_prose is not usable here: it deletes [Exert] and the ability labels,
# which is exactly where activation costs live. This does the same cleanup
# but keeps the cost structure intact.
# ---------------------------------------------------------------------
# Split before an ALLCAPS label so "...free.[SLIPPERY SPELL] While..." becomes
# two abilities. Requires no lowercase inside the brackets, so [Exert] (which
# is a cost token, not a label) is left alone.
_LABEL_SPLIT = re.compile(r"(?=\[[^a-z\]]{3,}\])")

# Some exports fold the ink cost inside the label: Luisa Madrigal ships as
# "[I CAN TAKE IT 1] Ink -" where the ability is "I CAN TAKE IT" at a cost of
# 1 Ink. Recover the digit rather than dropping the cost with the label.
_LABEL_COST = re.compile(r"\[[^\]]*?\s+(\d+)\]\s*Ink\b")

_BOOST_KW = re.compile(r"\bBoost\s+\d+\s*Ink\b", re.IGNORECASE)


def ability_lines(desc):
    text = clean_text(desc)
    text = text.replace("\u2019", "'")             # curly apostrophe
    text = re.sub(r"\([^)]*\)", "", text)          # reminder text
    for kw in parse_printed_keywords(desc):        # Shift N, Evasive, ...
        text = _KW_PATTERNS[kw].sub(" ", text, count=1)
    text = _BOOST_KW.sub(" ", text)                # engine reads Boost itself
    text = _LABEL_COST.sub(r"\1 Ink", text)
    text = re.sub(r"(?<=\s)[\u2013-](?=\s)", "\u2014", text)   # separator
    text = _LABEL_SPLIT.sub("\n", text)
    return [l.strip() for l in re.split(r"\n", text) if l.strip()]


def parse_activated(desc):
    """List of activated entries, or None if ANY ability line on the card
    fails to parse. All-or-nothing per card: a half-parsed card would play
    with only some of its abilities and silently misreport its win rate."""
    lines = ability_lines(desc)
    if not any(_ACT_HEAD.match(l) for l in lines):
        return None
    out = []
    for line in lines:
        m = _ACT_HEAD.match(line)
        if not m:
            # A card may mix an activated ability with a static one
            # (Buzz's Arm: MISSING PIECE + SOME ASSEMBLY REQUIRED). Still
            # all-or-nothing: an unparsed line rejects the whole card.
            st = parse_static_line(line)
            if st is None:
                return None
            out.extend(st if isinstance(st, list) else [st])
            continue
        cost = _parse_cost(m.group(1))
        if cost is None:
            return None
        body = m.group(2).strip()
        cond = None
        cm = _COND_PREFIX.match(body)
        if cm:
            cond = {"type": "cards_played_this_turn",
                    "count": int(cm.group(1))}
            body = body[cm.end():].strip()
            if body and body[0].islower():
                body = body[0].upper() + body[1:]
        eff = match_clause(body)
        if eff is None:
            return None
        ent = {"trigger": "activated", "cost": cost, "effect": eff}
        if cond is None and eff.get("type") == "move_damage":
            # gate availability so the ability is not offered as a no-op
            cond = {"type": "damage_to_move", "dump_at": eff.get("dump_at")}
        if cond:
            ent["condition"] = cond
        out.append(ent)
    return out or None


_SENT = re.compile(r"(?<=\.)\s+")


def parse_by_clauses(prose):
    """Split into sentences; require EVERY sentence to match a clause.
    Returns list of effect dicts, or None."""
    whole = match_clause(prose)
    if whole:
        return [whole]         # multi-sentence composite (e.g. dig-N)
    sents = [x.strip() for x in _SENT.split(prose) if x.strip()]
    if len(sents) < 2:
        return None            # single-sentence cards are the whole-text path
    effects = []
    for sent in sents:
        hit = match_clause(sent)
        if hit is None:
            return None        # one unrecognized sentence rejects the card
        effects.append(hit)
    return effects




_WATCH_PLAY_CHAR = re.compile(
    r"^Whenever you play a character,\s*(?P<rest>.+)$", re.IGNORECASE)
_MAY_PAY_BANISH = re.compile(
    r"^you may pay (?P<ink>\d+) Ink and banish this (?:item|character|location) to\s*",
    re.IGNORECASE)


def parse_play_character_watcher(prose):
    """'Whenever you play a character, <optional cost> <clause>'."""
    m = _WATCH_PLAY_CHAR.match(prose.strip())
    if not m:
        return None
    rest = m.group("rest").strip()
    cost = None
    cm = _MAY_PAY_BANISH.match(rest)
    if cm:
        cost = {"ink": int(cm.group("ink")), "banish_self": True}
        rest = rest[cm.end():].strip()
    else:
        mp = _MAY_PAY.match(rest)
        if mp:
            cost = {"ink": int(mp.group(1))}
            rest = rest[mp.end():].strip()
        else:
            rest = _MAY.sub("", rest)
    if rest and rest[0].islower():
        rest = rest[0].upper() + rest[1:]
    effects = parse_by_clauses(rest)
    if not effects:
        return None
    return cost, effects



_ON_BANISH = re.compile(r"^When this character is banished,\s*(?P<rest>.+)$",
                        re.IGNORECASE)
_CLASS_COND = re.compile(
    r"^if you have an? (?P<a>[A-Za-z' ]+?)(?: or (?P<b>[A-Za-z' ]+?))? "
    r"character in play,\s*", re.IGNORECASE)

# Classification names as they appear in the card data.
_CLASS_CANON = {"seven dwarfs": "Seven Dwarfs", "princess": "Princess",
                "hero": "Hero", "villain": "Villain", "ally": "Ally",
                "dreamborn": "Dreamborn", "storyborn": "Storyborn",
                "floodborn": "Floodborn", "captain": "Captain",
                "pirate": "Pirate", "sorcerer": "Sorcerer", "queen": "Queen",
                "king": "King", "prince": "Prince", "knight": "Knight",
                "musketeer": "Musketeer", "inventor": "Inventor",
                "detective": "Detective", "mentor": "Mentor",
                "madrigal": "Madrigal", "puppy": "Puppy", "titan": "Titan",
                "fairy": "Fairy", "deity": "Deity", "alien": "Alien",
                "robot": "Robot", "tigger": "Tigger", "broom": "Broom",
                "entangled": "Entangled", "racer": "Racer", "seer": "Seer"}


def parse_on_banish(prose):
    """'When this character is banished, [if <classification>,] <clause>'."""
    m = _ON_BANISH.match(prose.strip())
    if not m:
        return None
    rest = m.group("rest").strip()
    cond = None
    cm = _CLASS_COND.match(rest)
    if cm:
        names = [cm.group("a"), cm.group("b")]
        canon = []
        for n in names:
            if not n:
                continue
            c = _CLASS_CANON.get(n.strip().lower())
            if c is None:
                return None        # unknown classification -> leave the gap
            canon.append(c)
        cond = {"type": "you_have_classification", "any_of": canon}
        rest = rest[cm.end():].strip()
    rest = _MAY.sub("", rest)
    if rest and rest[0].islower():
        rest = rest[0].upper() + rest[1:]
    effects = parse_by_clauses(rest)
    if not effects:
        return None
    return cond, effects


def parse_static_card(desc):
    """Cards whose only text is static lines (Randall Boggs, Ursula)."""
    lines = ability_lines(desc)
    if not lines:
        return None
    out = []
    for line in lines:
        st = parse_static_line(line)
        if st is None:
            return None            # all-or-nothing, as elsewhere
        out.extend(st if isinstance(st, list) else [st])
    return out or None


# ---------------------------------------------------------------------
# Triggered preambles. "When you play this character, <clause>" is the same
# effect as a bare action clause with a different trigger, so route the
# remainder through the clause table instead of writing a second copy of
# every template. An optional "you may pay N Ink to" prefix becomes a cost
# on the entry, which schema._run pays before applying the effect.
# ---------------------------------------------------------------------
_PREAMBLES = [
    (re.compile(r"^When you play this character,\s*", re.IGNORECASE), "on_play"),
    (re.compile(r"^Whenever this character quests,\s*", re.IGNORECASE), "on_quest"),
]
_MAY_PAY = re.compile(r"^you may pay (\d+) Ink to\s*", re.IGNORECASE)
_MAY = re.compile(r"^you may\s+", re.IGNORECASE)
_TRIG_CONDS = [
    (re.compile(r"^if you have a character with (Evasive|Ward|Reckless|Support) "
                r"in play,\s*", re.IGNORECASE),
     lambda m: {"type": "you_have_keyword", "keyword": m.group(1).lower()}),
]


def parse_triggered(prose):
    """Return (trigger, cost_or_None, [effects]) or None."""
    for rx, trig in _PREAMBLES:
        m = rx.match(prose)
        if not m:
            continue
        rest = prose[m.end():].strip()
        cond = None
        for crx, build in _TRIG_CONDS:
            cm = crx.match(rest)
            if cm:
                cond = build(cm)
                rest = rest[cm.end():].strip()
                break
        cost = None
        mp = _MAY_PAY.match(rest)
        if mp:
            cost = {"ink": int(mp.group(1))}
            rest = rest[mp.end():].strip()
            if rest and rest[0].islower():
                rest = rest[0].upper() + rest[1:]
        effects = parse_by_clauses(rest)
        if effects is None:
            stripped = _MAY.sub("", rest)
            if stripped != rest:
                if stripped and stripped[0].islower():
                    stripped = stripped[0].upper() + stripped[1:]
                effects = parse_by_clauses(stripped)
        if effects:
            return trig, cond, cost, effects
    return None


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

    # Activated abilities (cost -- effect), parsed off the raw text.
    acts = parse_activated(desc)
    if acts:
        for a in acts:
            a["confidence"] = "medium"
            a["source"] = _src(desc)
        return acts

    # Static-only cards (no activated ability, no trigger preamble).
    st = parse_static_card(desc)
    if st:
        for e in st:
            e["confidence"] = "medium"
            e["source"] = _src(desc)
        return st

    # "When this character is banished" triggers.
    ob = parse_on_banish(prose)
    if ob:
        cond, effects = ob
        ents = []
        for e in effects:
            ent = {"trigger": "on_banish", "effect": e,
                   "confidence": "medium", "source": _src(desc)}
            if cond:
                ent["condition"] = cond
            ents.append(ent)
        return ents

    # "Whenever you play a character" watchers.
    watch = parse_play_character_watcher(prose)
    if watch:
        cost, effects = watch
        ents = []
        for e in effects:
            ent = {"trigger": "on_play_character", "effect": e,
                   "confidence": "medium", "source": _src(desc)}
            if cost:
                ent["cost"] = cost
                if cost.get("banish_self") and e.get("type") == "deal_damage":
                    # one-shot removal: hold it until it actually trades up
                    ent["condition"] = {"type": "damage_would_banish",
                                        "amount": e.get("amount", 1)}
            ents.append(ent)
        return ents

    # Triggered preamble + clause body.
    trig = parse_triggered(prose)
    if trig:
        trigger, cond, cost, effects = trig
        ents = []
        for e in effects:
            ent = {"trigger": trigger, "effect": e,
                   "confidence": "medium", "source": _src(desc)}
            if cond:
                ent["condition"] = cond
            if cost:
                ent["cost"] = cost
            ents.append(ent)
        return ents

    # Single-sentence bare-imperative (action cards): try clauses directly.
    single = match_clause(prose)
    if single:
        return [{"trigger": "on_play", "effect": single,
                 "confidence": "high", "source": _src(desc)}]

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
