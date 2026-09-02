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
                              _BARE_NAME as _KW_BARE_NAME,
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
    # ...and the unbracketed ALLCAPS labels the keyword layer also recognises.
    t = _KW_BARE_NAME.sub(" ", t)
    t = t.replace("\u2019", "'")             # curly apostrophe
    t = t.replace("{}", " ")                 # ink/lore symbol notation
    t = _BOOST_KW.sub(" ", t)                # engine reads Boost itself
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
@clause(r"[Cc]hosen character gains (Rush|Evasive|Ward|Reckless) this turn\.?")
def _c(m):
    return {"type": "grant_keyword", "keyword": m.group(1).lower(),
            "target": "best_quester", "duration": "eot"}


@clause(r"[Cc]hosen character of yours gains (Evasive|Ward|Rush|Reckless) "
        r"until the start of your next turn\.?")
def _c(m):
    return {"type": "grant_keyword", "keyword": m.group(1).lower(),
            "target": "best_quester", "duration": "until_your_next"}


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



# --- Phase 9 clauses -------------------------------------------------
@clause(r"[Dd]eal (\d+) damage to that character\.?")
def _c(m):
    # ctx["char"] is the character that was just damaged.
    return {"type": "deal_damage", "amount": int(m.group(1)), "target": "self"}


@clause(r"[Dd]eal (\d+) damage to another chosen character\.?")
def _c(m):
    return {"type": "deal_damage", "amount": int(m.group(1)),
            "target": "chosen_opposing", "exclude_previous": True}


@clause(r"[Rr]eturn an action card named ([A-Za-z' !.-]+?) from your discard "
        r"to your hand\.?")
def _c(m):
    return {"type": "return_from_discard",
            "filter": {"card_type": "action", "name": m.group(1).strip()}}


@clause(r"[Rr]eturn an? ([A-Za-z ]+?) character card from your discard "
        r"to your hand\.?")
def _c(m):
    c = _CLASS_CANON.get(m.group(1).strip().lower())
    if c is None:
        return None
    return {"type": "return_from_discard",
            "filter": {"card_type": "character", "classification": c}}


@clause(r"[Rr]eturn all cards under it to your hand\.?")
def _c(m):
    return {"type": "return_cards_under"}


@clause(r"[Pp]lay or shift an? ([A-Za-z ]+?) character with cost (\d+) or less "
        r"for free\.?")
def _c(m):
    c = _CLASS_CANON.get(m.group(1).strip().lower())
    if c is None:
        return None
    return {"type": "play_from_hand_free", "max_cost": int(m.group(2)),
            "filter": {"card_type": "character", "classification": c}}


@clause(r"[Dd]raw a card, then choose and discard a card\.?")
def _c(m):
    return {"type": "draw_then_discard", "amount": 1}



# --- Phase 11 clauses ------------------------------------------------
@clause(r"[Dd]eal (\d+) damage to the challenging character\.?")
def _c(m):
    # ctx["char"] is the attacker in an on_opposing_challenge dispatch.
    return {"type": "deal_damage", "amount": int(m.group(1)), "target": "self"}


@clause(r"[Bb]anish chosen item\.?")
def _c(m):
    return {"type": "banish_item"}


@clause(r"[Dd]eal (\d+) damage to up to (\d+) chosen characters\.?")
def _c(m):
    return {"type": "deal_damage_multi", "amount": int(m.group(1)),
            "count": int(m.group(2))}


@clause(r"[Cc]hosen character of yours gains Resist \+(\d+) "
        r"until the start of your next turn\.?")
def _c(m):
    return {"type": "grant_resist", "amount": int(m.group(1)),
            "duration": "until_your_next"}


@clause(r"[Tt]hey choose and discard a card\.?")
def _c(m):
    return {"type": "opponent_discard", "amount": 1}


@clause(r"[Tt]his character may enter play exerted to give chosen character "
        r"(Challenger|Resist) \+(\d+) until the start of your next turn\.?")
def _c(m):
    return {"type": "enter_exerted_for",
            "then": _grant(m.group(1), m.group(2),
                           duration="until_your_next")}


@clause(r"[Tt]his character may enter play exerted to deal (\d+) damage "
        r"to chosen damaged character\.?")
def _c(m):
    return {"type": "enter_exerted_for",
            "then": {"type": "deal_damage", "amount": int(m.group(1)),
                     "filter": {"damaged": True}}}



# --- Phase 12 clauses ------------------------------------------------
@clause(r"[Dd]eal (\d+) damage to each opposing damaged character\.?")
def _c(m):
    return {"type": "damage_each", "amount": int(m.group(1)),
            "filter": {"damaged": True}}


@clause(r"[Gg]ain lore equal to (?:her|his|their|its) Strength, "
        r"to a maximum of (\d+) lore\.?")
def _c(m):
    return {"type": "gain_lore_equal_strength", "max": int(m.group(1))}


@clause(r"[Yy]ou may exert chosen damaged character\.?")
def _c(m):
    return {"type": "exert_chosen", "filter": {"damaged": True}}


@clause(r"[Ee]xert chosen damaged character\.?")
def _c(m):
    return {"type": "exert_chosen", "filter": {"damaged": True}}


@clause(r"[Bb]anish all locations\.?")
def _c(m):
    return {"type": "banish_all_locations"}


@clause(r"[Tt]his character gains (Evasive|Ward|Rush|Reckless) "
        r"until the start of your next turn\.?")
def _c(m):
    return {"type": "grant_keyword", "keyword": m.group(1).lower(),
            "target": "self", "duration": "until_your_next"}


@clause(r"[Cc]hosen player reveals their hand\.?")
def _c(m):
    return {"type": "reveal_hand"}


@clause(r"[Cc]hosen opposing character gains (Reckless|Evasive|Ward|Rush) "
        r"until the start of your next turn\.?")
def _c(m):
    return {"type": "grant_keyword_opposing", "keyword": m.group(1).lower(),
            "duration": "until_your_next"}


@clause(r"[Yy]our characters with (Evasive|Ward|Reckless) get \+(\d+) "
        r"(Lore|Strength|Willpower) this turn\.?")
def _c(m):
    return {"type": "buff_your_keyword_chars", "keyword": m.group(1).lower(),
            "amount": int(m.group(2)),
            "stat": {"lore": "lore", "strength": "str",
                     "willpower": "will"}[m.group(3).lower()]}


@clause(r"[Dd]raw (\d+) cards, then choose and discard (\d+) cards\.?")
def _c(m):
    return {"type": "draw_then_discard", "amount": int(m.group(1)),
            "discard": int(m.group(2))}


@clause(r"[Dd]raw (\d+) cards, then choose and discard a card\.?")
def _c(m):
    return {"type": "draw_then_discard", "amount": int(m.group(1)),
            "discard": 1}


@clause(r"[Tt]he challenging player chooses and discards a card\.?")
def _c(m):
    # ctx p is the defender's controller, so "opponent" is the challenger.
    return {"type": "opponent_discard", "amount": 1}


@clause(r"[Ee]ach opponent chooses and discards a card\.?")
def _c(m):
    return {"type": "opponent_discard", "amount": 1}


@clause(r"[Yy]ou pay (\d+) Ink less for the next character named "
        r"([A-Za-z' .-]+?) you play this turn\.?")
def _c(m):
    return {"type": "cost_reduce", "amount": int(m.group(1)),
            "filter": "character", "name": m.group(2).strip()}



# --- Phase 13 clauses ------------------------------------------------
@clause(r"[Dd]eal (\d+) damage to chosen opposing damaged character\.?")
def _c(m):
    return {"type": "deal_damage", "amount": int(m.group(1)),
            "filter": {"damaged": True}}


@clause(r"[Pp]ut (\d+) damage counters? on each opposing character\.?")
def _c(m):
    return {"type": "damage_counter_each_opposing", "amount": int(m.group(1))}


@clause(r"[Bb]anish chosen item or location and all other items or locations "
        r"with the same name\.?")
def _c(m):
    return {"type": "banish_same_name"}


@clause(r"[Yy]ou may put the top card of your deck facedown under one of your "
        r"characters or locations with Boost\.?")
def _c(m):
    return {"type": "put_top_under_boosted"}


@clause(r"[Yy]ou may move him and one of your other characters to the same "
        r"location for free\.?")
def _c(m):
    return {"type": "move_two_to_location"}


@clause(r"[Dd]eal (\d+) damage to chosen opposing character\. If (\d+) or more "
        r"cards were put into your discard this turn, deal (\d+) damage instead\.?")
def _c(m):
    return {"type": "damage_conditional", "amount": int(m.group(1)),
            "upgraded_amount": int(m.group(3)),
            "upgrade_if": {"type": "discards_this_turn_at_least",
                           "count": int(m.group(2))}}


@clause(r"[Ee]ach player chooses and discards a card\.?")
def _c(m):
    return {"type": "opponent_discard", "amount": 1}



# --- Phase 14 clauses ------------------------------------------------
@clause(r"[Tt]his character gets \+(\d+) Strength this turn\.?")
def _c(m):
    return {"type": "stat_mod", "stat": "str", "amount": int(m.group(1)),
            "target": "self", "duration": "eot"}


@clause(r"[Ee]ach of your characters gets \+(\d+) Strength this turn\.?")
def _c(m):
    return {"type": "buff_all_yours", "stat": "str", "amount": int(m.group(1))}


@clause(r"[Yy]ou may banish chosen item\.?")
def _c(m):
    return {"type": "banish_item"}


@clause(r"[Ee]ach opposing character gets \-(\d+) until the start of your "
        r"next turn\.?")
def _c(m):
    # The stat glyph is stripped by clean-up; every printed card with this
    # wording is a Strength debuff.
    return {"type": "debuff_all_opposing", "stat": "str",
            "amount": int(m.group(1)), "duration": "until_your_next"}


@clause(r"[Ee]ach opponent chooses and discards (\d+) cards\.?")
def _c(m):
    return {"type": "opponent_discard", "amount": int(m.group(1))}


@clause(r"[Yy]ou may banish the challenging character\.?")
def _c(m):
    return {"type": "banish_target"}


@clause(r"[Cc]hosen opposing character gains (Reckless|Evasive|Ward|Rush) "
        r"during their next turn\.?")
def _c(m):
    return {"type": "grant_keyword_opposing", "keyword": m.group(1).lower(),
            "duration": "until_your_next"}


@clause(r"[Rr]eturn a song card with cost (\d+) or less from your discard "
        r"to your hand\.?")
def _c(m):
    return {"type": "return_from_discard",
            "filter": {"card_type": "song", "max_cost": int(m.group(1))}}


@clause(r"[Ee]ach player may draw a card\.?")
def _c(m):
    return {"type": "each_player_draw", "amount": 1}



# --- Phase 15 clauses ------------------------------------------------
@clause(r"[Cc]hosen character gets \+(\d+) this turn\.?")
def _c(m):
    # the stat glyph is stripped in clean-up; this wording is always Strength
    return {"type": "stat_mod", "stat": "str", "amount": int(m.group(1)),
            "target": "chosen_character", "duration": "eot"}


_TWO_KEYWORDS = re.compile(
    r"[Cc]hosen character gains (Alert|Evasive|Ward|Rush) and "
    r"(Challenger|Resist) \+(\d+) this turn\.?")

_DRAIN_AND_GAIN = re.compile(
    r"[Ee]ach opponent loses (\d+) lore and you gain (\d+) lore\.?")

_STAT_AND_KEYWORD = re.compile(
    r"[Cc]hosen character gets \+(\d+) Strength and gains "
    r"(Evasive|Ward|Rush|Reckless) until the start of your next turn\.?")


@clause(r"[Rr]eady him\.?|[Rr]eady her\.?|[Rr]eady them\.?")
def _c(m):
    return {"type": "ready_self"}


@clause(r"[Yy]ou may put the top card of your deck under them facedown\.?")
def _c(m):
    return {"type": "put_top_under_self"}


@clause(r"[Ee]ach opponent chooses and discards a card for each card under "
        r"(?:him|her|them|it)\.?")
def _c(m):
    return {"type": "opponent_discard_per_card_under"}


@clause(r"[Yy]ou may move him to one of your locations for free\.?")
def _c(m):
    return {"type": "move_self_to_location"}


@clause(r"[Gg]ain (\d+) lore\.?")
def _c(m):
    return {"type": "gain_lore", "amount": int(m.group(1))}



# --- Phase 16 clauses ------------------------------------------------
@clause(r"[Pp]ut the top (\d+) cards of your deck into your discard\.?")
def _c(m):
    return {"type": "mill_self", "amount": int(m.group(1))}


@clause(r"[Yy]ou may put the top (\d+) cards of your deck into your discard\.?")
def _c(m):
    return {"type": "mill_self", "amount": int(m.group(1))}


@clause(r"[Rr]eady chosen character\. They can't quest for the rest of "
        r"this turn\.?")
def _c(m):
    return {"type": "ready_chosen", "no_quest": True}



# --- Phase 17 clauses ------------------------------------------------
@clause(r"[Bb]anish chosen character\.?")
def _c(m):
    return {"type": "banish_chosen"}


@clause(r"[Ee]xert all opposing characters with (\d+) Strength or less\.?")
def _c(m):
    return {"type": "exert_all_opposing",
            "filter": {"max_strength": int(m.group(1))}}


@clause(r"[Ee]xert chosen opposing character with (\d+) Strength or less\.?")
def _c(m):
    return {"type": "exert_chosen",
            "filter": {"max_strength": int(m.group(1))}}


@clause(r"[Dd]raw (\d+) cards\. Then, discard a card at random\.?")
def _c(m):
    return {"type": "draw_then_discard_random", "amount": int(m.group(1)),
            "discard": 1}


@clause(r"[Yy]ou may ready chosen character\. If you do, they can't quest "
        r"for the rest of this turn\.?")
def _c(m):
    return {"type": "ready_chosen", "no_quest": True}



# --- Cluster A: the ready-then-quest-lock idiom ----------------------
# The same rider appears on nine cards in four shapes: ready this character,
# ready a chosen one, ready another chosen one, and the plain "Ready chosen
# character." on an action.
@clause(r"[Rr]eady this character\. (?:He|She|They|It) can't quest for the "
        r"rest of this turn\.?")
def _c(m):
    return {"type": "ready_self", "no_quest": True}


@clause(r"[Yy]ou may ready this character\. If you do, (?:he|she|they|it) "
        r"can't quest for the rest of this turn\.?")
def _c(m):
    return {"type": "ready_self", "no_quest": True}


@clause(r"[Yy]ou may ready another chosen character\. If you do, "
        r"(?:they|he|she|it) can't quest for the rest of this turn\.?")
def _c(m):
    return {"type": "ready_chosen", "no_quest": True, "exclude_self": True}


@clause(r"[Rr]eady this character\. If you do, (?:he|she|they|it) can't quest "
        r"for the rest of this turn\.?")
def _c(m):
    return {"type": "ready_self", "no_quest": True}


@clause(r"[Rr]eady (?:her|him|them)\. (?:He|She|They|It) can't quest for the "
        r"rest of this turn\.?")
def _c(m):
    return {"type": "ready_self", "no_quest": True}



# --- Cluster C: locations and movement -------------------------------
@clause(r"[Yy]ou may move one of your other characters to that location for "
        r"free\. If you do, draw a card\.?")
def _c(m):
    return {"type": "move_other_here",
            "then": {"type": "draw", "amount": 1}}


@clause(r"[Yy]ou may move him and one of your other characters to the same "
        r"location for free\. If you do, the other character gets \+(\d+) "
        r"Strength this turn\.?")
def _c(m):
    return {"type": "move_two_to_location",
            "buff": {"stat": "str", "amount": int(m.group(1))}}


@clause(r"[Pp]ut the top card of your deck under this location facedown\.?")
def _c(m):
    return {"type": "put_top_under_source"}


@clause(r"[Pp]ut the top card of your deck into your discard\.?")
def _c(m):
    return {"type": "mill_self", "amount": 1}


@clause(r"[Yy]ou may draw a card\.?")
def _c(m):
    return {"type": "draw", "amount": 1}



# --- Cluster D: cards under permanents -------------------------------
@clause(r"[Yy]ou may draw a card for each card that was under (?:him|her|them|it)\.?")
def _c(m):
    return {"type": "draw_per_card_under"}


@clause(r"[Gg]ain (\d+) lore\.?")
def _c(m):
    return {"type": "gain_lore", "amount": int(m.group(1))}



# --- Cluster G: banish watchers --------------------------------------
@clause(r"[Ee]ach opponent chooses and discards a card for each card that "
        r"was under (?:him|her|them|it)\.?")
def _c(m):
    return {"type": "opponent_discard_per_card_under"}


@clause(r"[Ee]ach opponent chooses and banishes one of their characters\.?")
def _c(m):
    return {"type": "opponent_banish_own"}


@clause(r"[Ee]ach of your characters gets \+(\d+) Strength this turn\.?")
def _c(m):
    return {"type": "buff_all_yours", "stat": "str", "amount": int(m.group(1))}


@clause(r"[Bb]anish chosen opposing character with (\d+) Strength or less\.?")
def _c(m):
    return {"type": "banish_chosen",
            "filter": {"max_strength": int(m.group(1))}}


@clause(r"[Cc]hosen character of yours gains (Evasive|Ward|Rush|Reckless) "
        r"until the start of your next turn\.?")
def _c(m):
    return {"type": "grant_keyword", "keyword": m.group(1).lower(),
            "target": "best_quester", "duration": "until_your_next"}



# --- Cluster E: discard-pile recursion --------------------------------
@clause(r"[Yy]ou may return an? (action|character|item) card with cost (\d+) "
        r"or less from your discard to your hand\.?")
def _c(m):
    return {"type": "return_from_discard",
            "filter": {"card_type": m.group(1).lower(),
                       "max_cost": int(m.group(2))}}


@clause(r"[Rr]eturn an? (action|character|item) card with cost (\d+) "
        r"or less from your discard to your hand\.?")
def _c(m):
    return {"type": "return_from_discard",
            "filter": {"card_type": m.group(1).lower(),
                       "max_cost": int(m.group(2))}}


@clause(r"[Pp]ut all ([A-Za-z ]+?) character cards from your discard on the "
        r"bottom of your deck in any order\.?")
def _c(m):
    cls = _CLASS_CANON.get(m.group(1).strip().lower())
    if cls is None:
        return None
    return {"type": "discard_to_bottom", "count": "all",
            "filter": {"card_type": "character", "classification": cls}}


@clause(r"[Yy]ou may discard your hand\. If you do, return a card from your "
        r"discard to your hand\.?")
def _c(m):
    return {"type": "discard_hand_then_return"}


@clause(r"[Bb]anish chosen character with (\d+) Strength or less\.?")
def _c(m):
    return {"type": "banish_chosen",
            "filter": {"max_strength": int(m.group(1))}}



# --- Clusters B and I -------------------------------------------------
@clause(r"[Yy]ou may put the top card of your deck into your inkwell "
        r"facedown and exerted\.?")
def _c(m):
    return {"type": "put_top_into_inkwell"}


@clause(r"[Yy]ou may exert all cards in your inkwell\.?")
def _c(m):
    return {"type": "exert_all_inkwell"}


@clause(r"[Ee]xert all cards in your inkwell\.?")
def _c(m):
    return {"type": "exert_all_inkwell"}


@clause(r"[Ee]xert chosen opposing character with (\d+) or less\.?")
def _c(m):
    # the Strength glyph is stripped in clean-up; this wording is Strength
    return {"type": "exert_chosen",
            "filter": {"max_strength": int(m.group(1))}}


@clause(r"[Rr]eturn chosen opposing character to their player's hand\.?")
def _c(m):
    return {"type": "return_to_hand", "side": "opposing"}


@clause(r"[Pp]ut the top card of your deck facedown under one of your "
        r"characters or locations(?: with Boost)?\.?")
def _c(m):
    return {"type": "put_top_under_boosted"}



# --- keyword grants to a chosen character -----------------------------
# Alert is boolean; Challenger and Resist are numeric and must carry their
# value, or the grant resolves to +0.
_KW_NUMERIC = {"challenger", "resist"}


def _grant(kw, amount, cls=None, duration="eot"):
    e = {"type": "grant_keyword", "keyword": kw.lower(),
         "target": "best_quester", "duration": duration}
    if amount is not None:
        e["amount"] = int(amount)
    if cls:
        e["target_filter"] = {"classification": cls}
    return e


@clause(r"[Cc]hosen character gains (Alert|Evasive|Ward|Rush|Reckless) "
        r"this turn\.?")
def _c(m):
    return _grant(m.group(1), None)


@clause(r"[Cc]hosen character gains (Challenger|Resist) \+(\d+) this turn\.?")
def _c(m):
    return _grant(m.group(1), m.group(2))


@clause(r"[Cc]hosen character gains (Challenger|Resist) \+(\d+) until the "
        r"start of your next turn\.?")
def _c(m):
    return _grant(m.group(1), m.group(2), duration="until_your_next")


@clause(r"[Cc]hosen ([A-Za-z ]+?) character gains (Alert|Evasive|Ward|Rush) "
        r"this turn\.?")
def _c(m):
    cls = _CLASS_CANON.get(m.group(1).strip().lower())
    if cls is None:
        return None
    return _grant(m.group(2), None, cls)


@clause(r"[Cc]hosen ([A-Za-z ]+?) character gains (Challenger|Resist) "
        r"\+(\d+) this turn\.?")
def _c(m):
    cls = _CLASS_CANON.get(m.group(1).strip().lower())
    if cls is None:
        return None
    return _grant(m.group(2), m.group(3), cls)


@clause(r"[Gg]ive chosen character (Challenger|Resist) \+(\d+) until the "
        r"start of your next turn\.?")
def _c(m):
    return _grant(m.group(1), m.group(2), duration="until_your_next")


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
            built = builder(m)
            if built is not None:
                return built
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
    r"((?:\[Exert\]|\{\}|\bexert\b|\d+\s*Ink|Banish this [a-z]+)"   # first cost token
    r"(?:\s*,\s*[^\u2014]{1,45}?)*)"                          # further cost tokens
    r"\s*\u2014\s*(.+)$")                                    # separator + effect


def _parse_cost(text):
    cost = {}
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if re.fullmatch(r"\[Exert\]|\{\}|exert", tok, re.IGNORECASE):
            cost["exert"] = True
            continue
        m = re.fullmatch(r"(\d+)\s*(?:Ink|\{\})", tok, re.IGNORECASE)
        if m:
            cost["ink"] = int(m.group(1))
            continue
        if re.fullmatch(r"Banish this (item|character|location)", tok, re.IGNORECASE):
            cost["banish_self"] = True
            continue
        if re.fullmatch(r"Banish (?:chosen character of yours|one of your "
                        r"characters)", tok, re.IGNORECASE):
            cost["banish_own_char"] = True
            continue
        m = re.fullmatch(r"Deal (\d+) damage to this character", tok,
                         re.IGNORECASE)
        if m:
            cost["self_damage"] = int(m.group(1))
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
_COND_DISCARDED = re.compile(r"^If you discarded a card this turn,\s*",
                             re.IGNORECASE)

_SELF_DISCOUNT = re.compile(
    r"If this is your first turn and you're not the first player, "
    r"you pay (\d+) Ink less to play this (?:character|item|action|location)\.?",
    re.IGNORECASE)


# The Strength symbol arrives as "{}" in this card export. Confirmed by
# Grandma Wu, whose Challenger reminder text reads "gets +2 {}" -- Challenger
# is unambiguously a Strength bonus.
_STAT_SYMBOL = "str"

_STATIC_TRIPLE = re.compile(
    r"(?:While|As long as|During) (?P<cond>.+?), (?:this character|she|he|they|it) "
    r"gets \+(?P<a1>\d+) (?P<s1>\{\}|Strength|Lore|Willpower), "
    r"\+(?P<a2>\d+) (?P<s2>\{\}|Strength|Lore|Willpower), and "
    r"\+(?P<a3>\d+) (?P<s3>\{\}|Strength|Lore|Willpower)\.?",
    re.IGNORECASE)

_STATIC_PER_UNDER = re.compile(
    r"This character gets \+(\d+) (Strength|Lore|Willpower) for each card "
    r"under (?:him|her|them|it)\.?", re.IGNORECASE)

_NO_QUEST_UNLESS = re.compile(
    r"This character can't quest or challenge unless you put a card under "
    r"(?:him|her|them|it) this turn\.?", re.IGNORECASE)

_STATIC_SELF = re.compile(
    r"(?:While|As long as|During|If) (?P<cond>.+?), (?:this character|she|he|they|it) "
    r"gets \+(?P<amt>\d+) (?P<stat>\{\}|Strength|Lore|Willpower)"
    r"(?: and \+(?P<amt2>\d+) (?P<stat2>\{\}|Strength|Lore|Willpower))?"
    r"(?: and gains (?P<kw>Evasive|Ward|Reckless|Rush|Support))?\.?",
    re.IGNORECASE)

# "While <cond>, it gains Resist +N." -- a numeric keyword, so it goes through
# the stat hook rather than the boolean keyword hook.
_STATIC_GAINS_KW = re.compile(
    r"(?:While|As long as|During) (?P<cond>.+?), (?:this character|she|he|they|it) "
    r"gains (?P<kw>Rush|Evasive|Ward|Reckless|Support)\.?", re.IGNORECASE)

_STATIC_RESIST = re.compile(
    r"(?:While|During) (?P<cond>.+?), (?:this character|she|he|they|it) gains "
    r"Resist \+(?P<amt>\d+)\.?", re.IGNORECASE)

_NO_READY_PLAIN = re.compile(
    r"This character can't ready at the start of your turn\.?",
    re.IGNORECASE)

_CANT_GAIN = re.compile(r"This character can't gain Evasive\.?", re.IGNORECASE)

_NO_READY = re.compile(
    r"If you have (?P<n>\d+) or more cards in your hand, "
    r"(?:this character|she|he|they|it) can't ready\.?", re.IGNORECASE)

# Modal abilities: "choose one:" followed by bullet options. The bullet glyph
# varies across the export, so accept the common ones.
_MODAL = re.compile(r"^choose one:?\s*(?P<rest>.+)$", re.IGNORECASE)
_BULLET = re.compile(r"\s*[\u2022*\-\u2013]\s+")

_SHIFT_ONTO = re.compile(
    r"You may pay \d+ Ink to play this on top of one of your characters named "
    r"(?P<a>[A-Za-z' .-]+?)(?: or (?P<b>[A-Za-z' .-]+?))?\.?", re.IGNORECASE)

_LOC_LORE = re.compile(
    r"While you have a character here, this location gets \+(\d+) Lore\.?",
    re.IGNORECASE)

_ENTERS_DAMAGE = re.compile(
    r"This character enters play with (\d+) damage\.?", re.IGNORECASE)

# "Your <classification> characters gain <keyword>." -- generalised from the
# Ward-only pattern so a location can grant Rush to your team
# (Beast's Castle - Overrun by the Vine).
_TEAM_KEYWORD = re.compile(
    r"Your (?:other )?([A-Za-z ]+?) characters gain "
    r"(Ward|Rush|Evasive|Reckless|Support)\.?", re.IGNORECASE)

_BOTTOM_FOR_PAYOFF = re.compile(
    r"[Yy]ou may put (\d+) (action|character|item) cards from your discard on "
    r"the bottom of your deck to give this character "
    r"(Rush|Evasive|Ward|Reckless) this turn\.?", re.IGNORECASE)

# same shape, but the payoff is a full clause rather than a keyword
_BOTTOM_THEN_CLAUSE = re.compile(
    r"[Yy]ou may put (\d+) (action|character|item) cards from your discard on "
    r"the bottom of your deck in any order\. If you do, (?P<rest>.+)$",
    re.IGNORECASE)

_DISCOUNT_PER_TYPE = re.compile(
    r"For each (action|character|item) card in your discard, you pay (\d+) "
    r"Ink less to play this character\.?", re.IGNORECASE)

_DISCOUNT_PER_DISCARD = re.compile(
    r"For each ([A-Za-z ]+?) character card in your discard, you pay (\d+) "
    r"Ink less to play this character\.?", re.IGNORECASE)

_DISCOUNT_IF_BANISHED = re.compile(
    r"If one of your ([A-Za-z ]+?) characters was banished this turn, "
    r"you pay (\d+) Ink less to play this character\.?", re.IGNORECASE)

_TEAM_STAT_PLAIN = re.compile(
    r"Your ([A-Za-z ]+?) characters get \+(\d+) (Strength|Lore|Willpower)\.?",
    re.IGNORECASE)

_LOC_KEYWORD = re.compile(
    r"While there's a character with (?P<kw>Evasive|Ward|Reckless) here, "
    r"this location gains (?P=kw)\.?", re.IGNORECASE)

_FREE_MOVE = re.compile(
    r"Your ([A-Za-z ]+?) characters can move here for free\.?", re.IGNORECASE)

_LOC_AURA = re.compile(
    r"Characters get \+(\d+) (Strength|Lore|Willpower)"
    r"(?: and \+(\d+) (Strength|Lore|Willpower))? while here\.?",
    re.IGNORECASE)

_TEAM_WARD = re.compile(
    r"Your other ([A-Za-z ]+?) characters gain Ward\.?", re.IGNORECASE)

_TEAM_STAT = re.compile(
    r"While this character is exerted, your other characters get "
    r"\+(\d+) (Strength|Lore|Willpower)\.?", re.IGNORECASE)

_LOC_LORE_PER = re.compile(
    r"This location gets \+(\d+) Lore for each character here\.?",
    re.IGNORECASE)

_STATIC_WARD = re.compile(
    r"While you have a character or location in play with a card under them, "
    r"(?:this character|she|he|they|it) gains Ward\.?", re.IGNORECASE)

_ENTERS_EXERTED = re.compile(
    r"This (?:item|character|location) enters play exerted\.?", re.IGNORECASE)

_SELF_DISCOUNT_NAMED = re.compile(
    r"If you have a character named ([A-Za-z' .-]+?) in play, "
    r"you pay (\d+) Ink less to play this character\.?", re.IGNORECASE)

_SELF_DISCOUNT_DISCARDS = re.compile(
    r"If (\d+) or more cards were put into your discard this turn, "
    r"you pay (\d+) Ink less to play this character\.?", re.IGNORECASE)

_SHIFT_ALIAS = re.compile(
    r"This character also counts as being named ([A-Za-z' .-]+?) for Shift\.?",
    re.IGNORECASE)

_STATIC_CONDS = [
    (re.compile(r"all cards in your inkwell are exerted", re.I),
     {"type": "inkwell_all_exerted"}),
    (re.compile(r"there's a card under this character", re.I),
     {"type": "has_card_under"}),
    (re.compile(r"this character has no damage", re.I),
     {"type": "self_undamaged"}),
    (re.compile(r"this character has damage", re.I), {"type": "self_damaged"}),
    (re.compile(r"this character has a card under (?:him|her|them|it)", re.I),
     {"type": "has_card_under"}),
    (re.compile(r"a character was banished this turn", re.I),
     {"type": "character_banished_this_turn"}),
    (re.compile(r"you have a ([A-Za-z ]+?) character in play", re.I), None),
    (re.compile(r"you have a character with (Singer|Evasive|Ward|Support|Reckless) in play",
                re.I), None),
    (re.compile(r"your turn", re.I), {"type": "your_turn"}),
    (re.compile(r"you have a character named ([A-Za-z' .-]+?) in play", re.I),
     None),
    (re.compile(r"an opposing damaged character is in play", re.I),
     {"type": "opposing_damaged_in_play"}),
    (re.compile(r"opponents.? turns", re.I), {"type": "opponents_turn"}),
    (re.compile(r"being challenged", re.I), {"type": "being_challenged"}),
    (re.compile(r"this character is at a location", re.I),
     {"type": "self_at_location"}),
    (re.compile(r"this character has at least one card under it", re.I),
     {"type": "has_card_under"}),
    (re.compile(r"there's a card under (?:him|her|them|it)", re.I),
     {"type": "has_card_under"}),
]


_NAMED_IN_PLAY = re.compile(
    r"you have a character named ([A-Za-z' .-]+?) in play", re.IGNORECASE)


_DISCARD_COUNT = re.compile(
    r"if (\d+) or more cards were put into your discard this turn",
    re.IGNORECASE)


def _static_cond(text):
    text = text.strip()
    if "," in text:
        parts = [x.strip() for x in text.split(",") if x.strip()]
        subs = [_static_cond(x) for x in parts]
        if len(subs) > 1 and all(subs):
            return {"type": "all_of", "all_of": subs}
    mc = re.fullmatch(r"you have an? ([A-Za-z ]+?) character in play", text,
                      re.IGNORECASE)
    if mc:
        cls = _classes(mc.group(1))
        if cls is None:
            return None
        return {"type": "you_have_classification", "any_of": cls}
    mk = re.fullmatch(r"you have a character with "
                      r"(Singer|Evasive|Ward|Support|Reckless) in play", text,
                      re.IGNORECASE)
    if mk:
        return {"type": "you_have_keyword", "keyword": mk.group(1).lower()}
    md = _DISCARD_COUNT.fullmatch(text)
    if md:
        return {"type": "discards_this_turn_at_least",
                "count": int(md.group(1))}
    m = _NAMED_IN_PLAY.fullmatch(text)
    if m:
        return {"type": "named_character_in_play", "name": m.group(1).strip()}
    for rx, c in _STATIC_CONDS:
        if c is not None and rx.fullmatch(text):
            return c
    return None


def _stat_name(tok):
    tok = tok.strip().lower()
    if tok == "{}":
        return _STAT_SYMBOL
    return {"strength": "str", "lore": "lore", "willpower": "will"}[tok]


def parse_static_self(line):
    """'While <condition>, this character gets +N <stat> [and gains KW].'"""
    line = line.strip()
    med = _ENTERS_DAMAGE.fullmatch(line)
    if med:
        return [{"trigger": "static",
                 "effect": {"type": "enters_with_damage",
                            "amount": int(med.group(1))}}]
    mtk = _TEAM_KEYWORD.fullmatch(line)
    if mtk:
        cls = _classes(mtk.group(1))
        if cls is None:
            return None
        return [{"trigger": "static",
                 "effect": {"type": "team_keyword",
                            "keyword": mtk.group(2).lower(),
                            "classification": cls[0]}}]
    mdt = _DISCOUNT_PER_TYPE.fullmatch(line)
    if mdt:
        return [{"trigger": "static",
                 "effect": {"type": "play_cost_reduction",
                            "amount": int(mdt.group(2)),
                            "per": "card_type_in_discard",
                            "card_type": mdt.group(1).lower()}}]
    mdd = _DISCOUNT_PER_DISCARD.fullmatch(line)
    if mdd:
        cls = _classes(mdd.group(1))
        if cls is None:
            return None
        return [{"trigger": "static",
                 "effect": {"type": "play_cost_reduction",
                            "amount": int(mdd.group(2)),
                            "per": "classification_in_discard",
                            "classification": cls[0]}}]
    mdb = _DISCOUNT_IF_BANISHED.fullmatch(line)
    if mdb:
        cls = _classes(mdb.group(1))
        if cls is None:
            return None
        return [{"trigger": "static",
                 "condition": {"type": "classification_banished_this_turn",
                               "name": cls[0]},
                 "effect": {"type": "play_cost_reduction",
                            "amount": int(mdb.group(2))}}]
    mtp = _TEAM_STAT_PLAIN.fullmatch(line)
    if mtp:
        cls = _classes(mtp.group(1))
        if cls is None:
            return None
        return [{"trigger": "static",
                 "effect": {"type": "team_stat",
                            "stat": _stat_name(mtp.group(3)),
                            "amount": int(mtp.group(2)),
                            "classification": cls[0],
                            "include_self": True}}]
    mlk = _LOC_KEYWORD.fullmatch(line)
    if mlk:
        return [{"trigger": "static",
                 "condition": {"type": "keyword_character_here",
                               "keyword": mlk.group("kw").lower()},
                 "effect": {"type": "static_location_keyword",
                            "keyword": mlk.group("kw").lower()}}]
    mfm = _FREE_MOVE.fullmatch(line)
    if mfm:
        cls = _classes(mfm.group(1))
        if cls is None:
            return None
        return [{"trigger": "static",
                 "effect": {"type": "free_move_here",
                            "classification": cls[0]}}]
    mla = _LOC_AURA.fullmatch(line)
    if mla:
        out = [{"trigger": "static",
                "effect": {"type": "location_aura_stat",
                           "stat": _stat_name(mla.group(2)),
                           "amount": int(mla.group(1))}}]
        if mla.group(3):
            out.append({"trigger": "static",
                        "effect": {"type": "location_aura_stat",
                                   "stat": _stat_name(mla.group(4)),
                                   "amount": int(mla.group(3))}})
        return out
    mt = _TEAM_WARD.fullmatch(line)
    if mt:
        cls = _classes(mt.group(1))
        if cls is None:
            return None
        return [{"trigger": "static",
                 "effect": {"type": "team_keyword", "keyword": "ward",
                            "classification": cls[0]}}]
    mts = _TEAM_STAT.fullmatch(line)
    if mts:
        return [{"trigger": "static",
                 "condition": {"type": "self_exerted"},
                 "effect": {"type": "team_stat",
                            "stat": _stat_name(mts.group(2)),
                            "amount": int(mts.group(1))}}]
    mlp = _LOC_LORE_PER.fullmatch(line)
    if mlp:
        return [{"trigger": "static",
                 "effect": {"type": "static_location_lore",
                            "amount": int(mlp.group(1)),
                            "per": "character_here"}}]
    ml = _LOC_LORE.fullmatch(line)
    if ml:
        return [{"trigger": "static",
                 "condition": {"type": "character_here"},
                 "effect": {"type": "static_location_lore",
                            "amount": int(ml.group(1))}}]
    if _STATIC_WARD.fullmatch(line):
        return [{"trigger": "static",
                 "condition": {"type": "permanent_with_card_under"},
                 "effect": {"type": "static_self_keyword", "keyword": "ward"}}]
    if _ENTERS_EXERTED.fullmatch(line):
        return [{"trigger": "static", "effect": {"type": "enters_exerted"}}]
    md = _SELF_DISCOUNT_NAMED.fullmatch(line)
    if md:
        return [{"trigger": "static",
                 "condition": {"type": "named_character_in_play",
                               "name": md.group(1).strip()},
                 "effect": {"type": "play_cost_reduction",
                            "amount": int(md.group(2))}}]
    mdd = _SELF_DISCOUNT_DISCARDS.fullmatch(line)
    if mdd:
        return [{"trigger": "static",
                 "condition": {"type": "discards_this_turn_at_least",
                               "count": int(mdd.group(1))},
                 "effect": {"type": "play_cost_reduction",
                            "amount": int(mdd.group(2))}}]
    if _CANT_GAIN.fullmatch(line):
        return [{"trigger": "static",
                 "effect": {"type": "static_self_keyword",
                            "keyword": "cant_gain_evasive"}}]
    if _NO_READY_PLAIN.fullmatch(line):
        return [{"trigger": "static", "effect": {"type": "static_no_ready"}}]
    mn = _NO_READY.fullmatch(line)
    if mn:
        return [{"trigger": "static",
                 "condition": {"type": "hand_size_at_least",
                               "count": int(mn.group("n"))},
                 "effect": {"type": "static_no_ready"}}]
    ma = _SHIFT_ALIAS.fullmatch(line)
    if ma:
        return [{"trigger": "static",
                 "effect": {"type": "shift_alias", "name": ma.group(1).strip()}}]
    mt3 = _STATIC_TRIPLE.fullmatch(line)
    if mt3:
        c = _static_cond(mt3.group("cond"))
        if c is None:
            return None
        return [{"trigger": "static", "condition": c,
                 "effect": {"type": "static_self_stat",
                            "stat": _stat_name(mt3.group("s%d" % i)),
                            "amount": int(mt3.group("a%d" % i))}}
                for i in (1, 2, 3)]
    mpu = _STATIC_PER_UNDER.fullmatch(line)
    if mpu:
        return [{"trigger": "static",
                 "effect": {"type": "static_self_stat",
                            "stat": _stat_name(mpu.group(2)),
                            "amount": int(mpu.group(1)),
                            "per": "cards_under"}}]
    if _NO_QUEST_UNLESS.fullmatch(line):
        return [{"trigger": "static",
                 "condition": {"type": "put_card_under_this_turn",
                               "scope": "self"},
                 "effect": {"type": "no_quest_or_challenge_unless"}}]
    mgk = _STATIC_GAINS_KW.fullmatch(line)
    if mgk:
        c = _static_cond(mgk.group("cond"))
        if c is None:
            return None
        return [{"trigger": "static", "condition": c,
                 "effect": {"type": "static_self_keyword",
                            "keyword": mgk.group("kw").lower()}}]
    mr = _STATIC_RESIST.fullmatch(line)
    if mr:
        c = _static_cond(mr.group("cond"))
        if c is None:
            return None
        return [{"trigger": "static", "condition": c,
                 "effect": {"type": "static_self_stat", "stat": "resist",
                            "amount": int(mr.group("amt"))}}]
    m = _STATIC_SELF.fullmatch(line)
    if not m:
        return None
    cond = _static_cond(m.group("cond"))
    if cond is None:
        return None                 # unrecognized condition -> leave the gap
    stat = _stat_name(m.group("stat"))
    out = [{"trigger": "static", "condition": cond,
            "effect": {"type": "static_self_stat", "stat": stat,
                       "amount": int(m.group("amt"))}}]
    if m.groupdict().get("amt2"):
        out.append({"trigger": "static", "condition": cond,
                    "effect": {"type": "static_self_stat",
                               "stat": _stat_name(m.group("stat2")),
                               "amount": int(m.group("amt2"))}})
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
    text = re.sub(r"(?<=\s)[\u2013-](?=\s)", "\u2014", text)   # separator
    # Split on ability labels FIRST: _LABEL_COST rewrites "[FINAL ARROW 1] Ink"
    # into "1 Ink", which would erase the boundary between two abilities that
    # the export runs together with no separator (Merida's Bow).
    text = _LABEL_SPLIT.sub("\n", text)
    text = _LABEL_COST.sub(r"\1 Ink", text)
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
        else:
            cd = _COND_DISCARDED.match(body)
            if cd:
                cond = {"type": "discarded_this_turn"}
                body = body[cd.end():].strip()
            if body and body[0].islower():
                body = body[0].upper() + body[1:]
        body = re.sub(r"\s*\{\}\s*", " ", body).strip()
        body = re.sub(r"\s+", " ", body)
        effs = parse_by_clauses(body)
        if not effs:
            return None
        # A multi-sentence activated ability is still ONE action with ONE
        # cost, so wrap it rather than emitting several entries (which would
        # expose the later halves as free activations).
        eff = effs[0] if len(effs) == 1 else {"type": "sequence",
                                              "effects": effs}
        ent = {"trigger": "activated", "cost": cost, "effect": eff}
        if cond is None and eff.get("type") == "move_damage":
            # gate availability so the ability is not offered as a no-op
            cond = {"type": "damage_to_move", "dump_at": eff.get("dump_at")}
        if cond:
            ent["condition"] = cond
        out.append(ent)
    return out or None


_SENT = re.compile(r"(?<=\.)\s+")
_THEN = re.compile(r"^Then,?\s+", re.IGNORECASE)


def parse_modal(text):
    """"choose one: <a> <b>" -> a single choose_one effect."""
    m = _MODAL.match(text.strip())
    if not m:
        return None
    parts = [x.strip() for x in _BULLET.split(m.group("rest")) if x.strip()]
    if len(parts) < 2:
        return None
    opts = []
    for part in parts:
        cond = None
        for crx, build in _TRIG_CONDS:
            cm = crx.match(part)
            if cm:
                c = build(cm)
                if c is None:
                    return None
                cond = c
                part = part[cm.end():].strip()
                break
        if part and part[0].islower():
            part = part[0].upper() + part[1:]
        e = match_clause(part)
        if e is None:
            return None        # all-or-nothing, as everywhere else
        if cond:
            e = dict(e)
            e["condition"] = cond
        opts.append(e)
    return {"type": "choose_one", "options": opts}


def parse_by_clauses(prose):
    """Split into sentences; require EVERY sentence to match a clause.
    Returns list of effect dicts, or None."""
    mbc = _BOTTOM_THEN_CLAUSE.fullmatch(prose.strip())
    if mbc:
        rest = mbc.group("rest").strip()
        if rest and rest[0].islower():
            rest = rest[0].upper() + rest[1:]
        inner = match_clause(rest)
        if inner:
            return [{"type": "discard_to_bottom", "count": int(mbc.group(1)),
                     "require_full": True,
                     "filter": {"card_type": mbc.group(2).lower()}},
                    {"type": "then_if_moved", "then": inner}]
    mbp = _BOTTOM_FOR_PAYOFF.fullmatch(prose.strip())
    if mbp:
        return [{"type": "discard_to_bottom", "count": int(mbp.group(1)),
                 "require_full": True,
                 "filter": {"card_type": mbp.group(2).lower()}},
                {"type": "then_if_moved",
                 "then": {"type": "grant_keyword",
                          "keyword": mbp.group(3).lower(),
                          "target": "self", "duration": "eot"}}]
    mtk = _TWO_KEYWORDS.fullmatch(prose.strip())
    if mtk:
        return [_grant(mtk.group(1), None),
                _grant(mtk.group(2), mtk.group(3))]
    mdg = _DRAIN_AND_GAIN.fullmatch(prose.strip())
    if mdg:
        return [{"type": "opponent_lose_lore", "amount": int(mdg.group(1))},
                {"type": "gain_lore", "amount": int(mdg.group(2))}]
    msk = _STAT_AND_KEYWORD.fullmatch(prose.strip())
    if msk:
        return [{"type": "stat_mod", "stat": "str",
                 "amount": int(msk.group(1)), "target": "best_quester",
                 "duration": "until_your_next"},
                {"type": "grant_keyword", "keyword": msk.group(2).lower(),
                 "target": "best_quester", "duration": "until_your_next"}]
    modal = parse_modal(prose)
    if modal:
        return [modal]
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
            # "Then, you may <clause>" chains a second effect onto the first.
            trimmed = _THEN.sub("", sent)
            trimmed = _MAY.sub("", trimmed)
            if trimmed != sent:
                if trimmed and trimmed[0].islower():
                    trimmed = trimmed[0].upper() + trimmed[1:]
                hit = match_clause(trimmed)
        if hit is None:
            return None        # one unrecognized sentence rejects the card
        effects.append(hit)
    return effects




_WATCH_PLAY_CHAR = re.compile(
    r"^Whenever you play "
    r"(?:a|another|this or another(?:\s+(?P<cls>[A-Za-z ]+?))?) "
    r"character(?: with (?P<minstr>\d+) Strength or more)?,\s*"
    r"(?P<rest>.+)$", re.IGNORECASE)
_MAY_PAY_BANISH = re.compile(
    r"^you may pay (?P<ink>\d+) Ink and banish this (?:item|character|location) to\s*",
    re.IGNORECASE)


def parse_play_character_watcher(prose):
    """'Whenever you play a character, <optional cost> <clause>'."""
    m = _WATCH_PLAY_CHAR.match(prose.strip())
    if not m:
        return None
    extra = {}
    if "this or another" in m.group(0).lower():
        extra["include_self"] = True
    if m.group("minstr"):
        extra["played_min_strength"] = int(m.group("minstr"))
    if m.group("cls"):
        cls = _classes(m.group("cls"))
        if cls is None:
            return None            # unknown classification -> leave the gap
        extra["played_classification"] = cls
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
    return cost, effects, extra



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
                "entangled": "Entangled", "racer": "Racer", "seer": "Seer", "toy": "Toy",
                "floodborn": "Floodborn", "hyena": "Hyena",
                "red panda": "Red Panda", "gargoyle": "Gargoyle"}


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


def parse_shift_onto(desc):
    """Shift reminder text naming more than one legal target."""
    txt = clean_text(desc).replace("\u2019", "'")
    m = _SHIFT_ONTO.search(txt)
    if not m or not m.group("b"):
        return None
    return {"trigger": "static",
            "effect": {"type": "shift_onto_names",
                       "names": [m.group("a").strip(), m.group("b").strip()]}}


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
    (re.compile(r"^When you play this (?:character|item|location),\s*",
                re.IGNORECASE), "on_play"),
    (re.compile(r"^When you shift this character,\s*", re.IGNORECASE), "on_shift"),
    (re.compile(r"^Whenever this character quests,\s*", re.IGNORECASE), "on_quest"),
    (re.compile(r"^Whenever one of your actions deals damage to an opposing "
                r"character,\s*", re.IGNORECASE), "on_action_damage"),
    (re.compile(r"^While this character is exerted, whenever an opposing "
                r"character challenges,\s*", re.IGNORECASE),
     "on_opposing_challenge"),
    (re.compile(r"^Whenever an opponent chooses this character for an action "
                r"or ability,\s*", re.IGNORECASE), "on_chosen_by_opponent"),
    (re.compile(r"^Whenever you play a location,\s*", re.IGNORECASE),
     "on_play_location"),
    (re.compile(r"^Whenever you play an action,\s*", re.IGNORECASE),
     "on_play_action"),
    (re.compile(r"^At the start of your turn,\s*", re.IGNORECASE),
     "on_turn_start"),
    (re.compile(r"^Whenever you put a card under this character,\s*",
                re.IGNORECASE), "on_card_under_self"),
    (re.compile(r"^Whenever you use the Boost ability of a character,\s*",
                re.IGNORECASE), "on_boost_used"),
    (re.compile(r"^While this character is at a location, whenever she "
                r"challenges another character,\s*", re.IGNORECASE),
     "on_challenges|atloc"),
    (re.compile(r"^Whenever he challenges another character,\s*",
                re.IGNORECASE), "on_challenges"),
    (re.compile(r"^Whenever this character challenges another character,\s*",
                re.IGNORECASE), "on_challenges"),
    (re.compile(r"^During opponents.? turns, whenever one of your other "
                r"characters is banished,\s*", re.IGNORECASE),
     "on_ally_banished|oppturn"),
    (re.compile(r"^During your turn, whenever one of your other "
                r"(?P<cls>[A-Za-z ]+?) characters is banished,\s*",
                re.IGNORECASE), "on_ally_banished|yourturn|cls"),
    (re.compile(r"^During your turn, whenever one of your other characters "
                r"is banished,\s*", re.IGNORECASE),
     "on_ally_banished|yourturn"),
    (re.compile(r"^During your turn, whenever one of your characters "
                r"is banished,\s*", re.IGNORECASE),
     "on_ally_banished|yourturn|self"),
    (re.compile(r"^During your turn, whenever an opposing character is "
                r"banished,\s*", re.IGNORECASE),
     "on_opposing_banished|yourturn"),
    (re.compile(r"^During your turn, whenever an? (?!opposing\b)"
                r"(?P<cls>[A-Za-z ]+?) character is banished,\s*",
                re.IGNORECASE), "on_ally_banished|yourturn|cls|self"),
    (re.compile(r"^During your turn, when this character is banished,\s*",
                re.IGNORECASE), "on_banish|yourturn"),
    (re.compile(r"^When this location is banished,\s*", re.IGNORECASE),
     "on_banish"),
    (re.compile(r"^Whenever one of your characters or locations with a card "
                r"under them is challenged,\s*", re.IGNORECASE),
     "on_ally_challenged|hasunder"),
    (re.compile(r"^Once during your turn, whenever this character moves to a "
                r"location,\s*", re.IGNORECASE), "on_move_self|once"),
    (re.compile(r"^Whenever you move a character here,\s*", re.IGNORECASE),
     "on_move_here"),
    (re.compile(r"^Once during your turn, whenever you move a character with "
                r"(?P<minstr>\d+) Strength or more here,\s*", re.IGNORECASE),
     "on_move_here|once|minstr"),
    (re.compile(r"^Whenever a character here challenges another character,\s*",
                re.IGNORECASE), "on_challenge_from_here"),
    (re.compile(r"^Whenever a character is challenged while here,\s*",
                re.IGNORECASE), "on_challenged_here"),
    (re.compile(r"^At the start of your turn,\s*", re.IGNORECASE),
     "on_turn_start"),
    (re.compile(r"^During your turn, whenever this character banishes another "
                r"character in a challenge,\s*", re.IGNORECASE),
     "on_banishes_in_challenge"),
    (re.compile(r"^Whenever an opponent plays a song,\s*", re.IGNORECASE),
     "on_opponent_song"),
    (re.compile(r"^When you play this character and whenever he quests,\s*",
                re.IGNORECASE), "on_play_and_quest"),
    (re.compile(r"^When this character is challenged and banished,\s*",
                re.IGNORECASE), "on_challenged_banished"),
    (re.compile(r"^Whenever this character is challenged,\s*", re.IGNORECASE),
     "on_challenged"),
    (re.compile(r"^Whenever one of your ([A-Za-z ]+?) characters is "
                r"challenged,\s*", re.IGNORECASE), "on_ally_challenged"),
    (re.compile(r"^When you play this character and when he leaves play,\s*",
                re.IGNORECASE), "on_play_and_leave"),
]
_MAY_PAY = re.compile(r"^you may pay (\d+) Ink to\s*", re.IGNORECASE)
_MAY = re.compile(r"^you may\s+", re.IGNORECASE)
def _classes(*names):
    """Canonical classification names, or None if any is unrecognized so the
    card is left as a visible gap rather than silently never matching."""
    out = []
    for n in names:
        if not n:
            continue
        c = _CLASS_CANON.get(n.strip().lower())
        if c is None:
            return None
        out.append(c)
    return out or None


_TRIG_CONDS = [
    (re.compile(r"^if you have a character with (Evasive|Ward|Reckless|Support) "
                r"in play,\s*", re.IGNORECASE),
     lambda m: {"type": "you_have_keyword", "keyword": m.group(1).lower()}),
    (re.compile(r"^if an? (?P<a>[A-Za-z ]+?)(?: or (?P<b>[A-Za-z ]+?))? "
                r"character is in play,\s*", re.IGNORECASE),
     lambda m: {"type": "classification_in_play",
                "any_of": _classes(m.group("a"), m.group("b"))}),
    (re.compile(r"^if you used Shift to play it,\s*", re.IGNORECASE),
     lambda m: {"type": "played_via_shift"}),
    (re.compile(r"^if there's a card under (?:him|her|them|it),\s*",
                re.IGNORECASE),
     lambda m: {"type": "has_card_under"}),
    (re.compile(r"^while this character is at a location,\s*", re.IGNORECASE),
     lambda m: {"type": "self_at_location"}),
    (re.compile(r"^if there's a card under this character,\s*", re.IGNORECASE),
     lambda m: {"type": "has_card_under"}),
    (re.compile(r"^if an opponent has more cards in their inkwell than you,"
                r"\s*", re.IGNORECASE),
     lambda m: {"type": "opponent_more_inkwell"}),
    (re.compile(r"^[Ii]f (\d+) or more(?: other)? cards were put into your "
                r"discard this turn,\s*", re.IGNORECASE),
     lambda m: {"type": "discards_this_turn_at_least",
                "count": int(m.group(1))}),
    (re.compile(r"^if you played (?:a|another) character this turn,\s*",
                re.IGNORECASE),
     lambda m: {"type": "played_another_character"}),
    (re.compile(r"^[Ii]f an opponent has more lore than you,\s*",
                re.IGNORECASE),
     lambda m: {"type": "opponent_has_more_lore"}),
]


def parse_triggered(prose):
    """Return (trigger, cost_or_None, [effects]) or None."""
    for rx, trig in _PREAMBLES:
        m = rx.match(prose)
        if not m:
            continue
        extra = {}
        if "|" in trig:
            parts = trig.split("|")
            trig = parts[0]
            if "once" in parts:
                extra["once_per_turn"] = True
            if "minstr" in parts and m.groupdict().get("minstr"):
                extra["moved_min_strength"] = int(m.group("minstr"))
            if "atloc" in parts:
                extra["condition"] = {"type": "self_at_location"}
            if "oppturn" in parts:
                extra["condition"] = {"type": "opponents_turn"}
            if "yourturn" in parts:
                extra["condition"] = {"type": "your_turn"}
            if "self" in parts:
                extra["include_self"] = True
            if "cls" in parts and m.groupdict().get("cls"):
                _cl = _classes(m.group("cls"))
                if _cl is None:
                    return None
                extra["banished_classification"] = _cl[0]
            if "hasunder" in parts:
                extra["defender_has_card_under"] = True
        if trig == "on_ally_challenged" and not extra.get(
                "defender_has_card_under"):
            cls = _classes(m.group(1)) if m.groups() else None
            if cls is None:
                return None
            extra["defender_classification"] = cls
        rest = prose[m.end():].strip()
        cond = None
        for crx, build in _TRIG_CONDS:
            cm = crx.match(rest)
            if cm:
                cond = build(cm)
                if cond is None or cond.get("any_of") is None \
                        and cond.get("type") == "classification_in_play":
                    return None       # unknown classification -> leave the gap
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
            return trig, cond, cost, effects, extra
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
    ents = _parse_one(prose, desc)
    if ents is not None:
        return ents

    # Multi-ability cards: one segment per printed ability. Each must parse on
    # its own or the whole card is left as a gap, so a card never ends up with
    # only half of its text implemented.
    segs = ability_lines(desc)
    if len(segs) > 1:
        out = []
        for seg in segs:
            # Static patterns read the raw segment: core_prose deletes the
            # "{}" stat glyph and the ability label they rely on.
            got = parse_static_line(seg)
            if got:
                got = got if isinstance(got, list) else [got]
                for e in got:
                    e.setdefault("confidence", "medium")
                    e.setdefault("source", _src(desc))
            else:
                got = _parse_one(core_prose(seg), desc)
            if not got:
                got = parse_activated(seg)
                if got:
                    for e in got:
                        e.setdefault("confidence", "medium")
                        e.setdefault("source", _src(desc))
            if not got:
                out = None
                break
            out.extend(got)
        if out:
            return out

    return [{"impl": "unimplemented", "text": _src(desc)}]


def _parse_one(prose, desc):
    """Parse a single ability's prose. Returns entries, or None."""
    if not prose:
        return None
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
        cost, effects, extra = watch
        ents = []
        for e in effects:
            ent = {"trigger": "on_play_character", "effect": e,
                   "confidence": "medium", "source": _src(desc)}
            ent.update(extra)
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
        trigger, cond, cost, effects, extra = trig
        ents = []
        # "When you play this character and when he leaves play" is two
        # triggers sharing one effect (Mickey Mouse - Snowboard Ace).
        triggers = ["on_play", "on_leave_play"] \
            if trigger == "on_play_and_leave" else \
            ["on_play", "on_quest"] if trigger == "on_play_and_quest" \
            else [trigger]
        for trigger in triggers:
         for e in effects:
            ent = {"trigger": trigger, "effect": e,
                   "confidence": "medium", "source": _src(desc)}
            ent.update(extra)
            if cond is None and e.get("type") == "enter_exerted_for" \
                    and (e.get("then") or {}).get("type") == "deal_damage":
                # the exert is paid up front, so only offer it when the
                # payoff exists (Lord MacGuffin WAIT FOR IT...). Only
                # meaningful when the payoff is damage -- Lord Macintosh
                # buffs a friendly character and must not be gated on it.
                cond = {"type": "opposing_damaged_present"}
            if cond:
                ent["condition"] = cond
            if cost:
                ent["cost"] = cost
            ents.append(ent)
        return ents

    # Single-sentence bare-imperative (action cards): try clauses directly.
    for crx, build in _TRIG_CONDS:
        cm = crx.match(prose)
        if not cm:
            continue
        c = build(cm)
        if c is None:
            break
        rest = prose[cm.end():].strip()
        if rest and rest[0].islower():
            rest = rest[0].upper() + rest[1:]
        effs = parse_by_clauses(rest)
        if effs:
            return [{"trigger": "on_play", "effect": e, "condition": c,
                     "confidence": "medium", "source": _src(desc)}
                    for e in effs]

    single = match_clause(prose)
    if single:
        ent = {"trigger": "on_play", "effect": single,
               "confidence": "high", "source": _src(desc)}
        if single.get("type") == "enter_exerted_for" \
                and (single.get("then") or {}).get("type") == "deal_damage":
            # gate only when the payoff is damage; a friendly buff payoff
            # (Lord Macintosh) has nothing to do with opposing damage
            ent["condition"] = {"type": "opposing_damaged_present"}
        return [ent]

    # Multi-sentence composition.
    effects = parse_by_clauses(prose)
    if effects:
        return [{"trigger": "on_play", "effect": e, "confidence": "medium",
                 "source": _src(desc)} for e in effects]

    return None      # caller decides: try segments, else mark unimplemented


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
