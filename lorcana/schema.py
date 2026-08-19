"""Phase 2 skeleton: data-driven ability schema.

Abilities are DATA, not code:

    {"trigger": "on_quest",
     "condition": {"type": "your_other_classification_count",
                   "classification": "Toy", "min": 2},
     "effect": {"type": "draw", "amount": 1}}

Entries live in abilities_manual.json (hand-authored, reviewed) keyed by card
name; a future abilities_auto.json (emitted offline by a template parser, then
human-reviewed) merges beneath it -- manual always wins. The engine calls
dispatch_* at each trigger point; unknown cards simply have no entries.

Currently implemented (deliberately small -- grow as templates demand):
  triggers:   on_play, on_quest
  conditions: your_other_classification_count, you_have_named, opponent_ahead
  effects:    draw, gain_lore, cost_reduce, stat_mod, deal_damage,
              draw_then_discard, grant_keyword

Escape hatch: an entry {"impl": "python"} documents that the card's logic
lives in abilities.py (used by the coverage report; no dispatch happens).
"""
import json, os

_REGISTRY = None
_HERE = os.path.dirname(__file__)
MANUAL_PATH = os.path.join(_HERE, "abilities_manual.json")
AUTO_PATH = os.path.join(_HERE, "abilities_auto.json")   # not present yet


def registry():
    """Load and merge ability data. Manual entries override auto entries."""
    global _REGISTRY
    if _REGISTRY is None:
        merged = {}
        if os.path.exists(AUTO_PATH):
            with open(AUTO_PATH) as f:
                merged.update(json.load(f))
        if os.path.exists(MANUAL_PATH):
            with open(MANUAL_PATH) as f:
                merged.update(json.load(f))
        _REGISTRY = merged
    return _REGISTRY


_HAND = None


def _hand_implemented():
    global _HAND
    if _HAND is None:
        try:
            from .abilities import HAND_IMPLEMENTED
            _HAND = set(HAND_IMPLEMENTED)
        except Exception:
            _HAND = set()
    return _HAND


def entries_for(card_name, trigger):
    # A card implemented in Python must never also run schema entries, or its
    # effects would double-apply. Manual entries are exempt (they are authored
    # deliberately and are expected to be the single source for that card).
    if card_name in _hand_implemented() and card_name not in (registry_manual_names()):
        return []
    ents = registry().get(card_name) or []
    return [e for e in ents if isinstance(e, dict) and e.get("trigger") == trigger]


_MANUAL_NAMES = None


def registry_manual_names():
    global _MANUAL_NAMES
    if _MANUAL_NAMES is None:
        names = set()
        if os.path.exists(MANUAL_PATH):
            try:
                with open(MANUAL_PATH) as f:
                    names = set(k for k in json.load(f).keys()
                                if not k.startswith("_"))
            except Exception:
                names = set()
        _MANUAL_NAMES = names
    return _MANUAL_NAMES


def has_schema_entry(card_name):
    ents = registry().get(card_name)
    return bool(ents)


# ---------------------------------------------------------------------
# Conditions. Each takes (g, p, ctx) -> bool. ctx may contain 'char'
# (the acting CharInPlay) and 'card'.
# ---------------------------------------------------------------------
def _cond_your_other_classification_count(g, p, ctx, cond):
    me = ctx.get("char")
    n = sum(1 for c in g.my_chars(p)
            if (me is None or c.uid != me.uid)
            and cond["classification"] in c.card.classifications)
    return n >= cond.get("min", 1)


def _cond_your_other_character_count(g, p, ctx, cond):
    """Count characters you control other than the acting one (any
    classification). Powers OHANA-style 'if you have N or more other
    characters in play' triggers."""
    me = ctx.get("char")
    n = sum(1 for c in g.my_chars(p) if me is None or c.uid != me.uid)
    return n >= cond.get("min", 1)


def _cond_you_have_named(g, p, ctx, cond):
    return any(c.card.base_name == cond["name"] for c in g.my_chars(p))


def _cond_opponent_ahead(g, p, ctx, cond):
    return g.players[1 - p].lore > g.players[p].lore


_CONDITIONS = {
    "your_other_classification_count": _cond_your_other_classification_count,
    "your_other_character_count": _cond_your_other_character_count,
    "you_have_named": _cond_you_have_named,
    "opponent_ahead": _cond_opponent_ahead,
}


def check_condition(g, p, ctx, cond):
    if not cond:
        return True
    fn = _CONDITIONS.get(cond.get("type"))
    if fn is None:
        g.emit(f"schema: unknown condition {cond.get('type')} (skipped)")
        return False
    return fn(g, p, ctx, cond)


# ---------------------------------------------------------------------
# Effects. Each takes (g, p, ctx, eff).
# ---------------------------------------------------------------------
def _eff_draw(g, p, ctx, eff):
    g.draw(p, eff.get("amount", 1))


def _eff_gain_lore(g, p, ctx, eff):
    g.gain_lore(p, eff.get("amount", 1), f"{ctx.get('card').base_name} (schema)")


def _eff_gain_lore_equal_to_exerted(g, p, ctx, eff):
    """Gain lore equal to another chosen exerted character's Lore.
    Heuristic choice: your own exerted character with the highest Lore,
    excluding the acting character itself."""
    me = ctx.get("char")
    cands = [c for c in g.my_chars(p)
             if c.exerted and (me is None or c.uid != me.uid)]
    if not cands:
        return
    tgt = max(cands, key=lambda c: g.eff_lore(c))
    amt = g.eff_lore(tgt)
    if amt > 0:
        g.gain_lore(p, amt, f"{ctx.get('card').base_name} (WHAT IS MY PATH?)")


def _eff_cost_reduce(g, p, ctx, eff):
    g.discounts.append({"owner": p, "amount": eff.get("amount", 1),
                        "filt": eff.get("filter", "character")})


def _resolve_target(g, p, ctx, spec):
    """Map a target spec string to a CharInPlay (or None)."""
    from . import abilities
    if spec in (None, "self"):
        return ctx.get("char")
    if spec == "chosen_opposing":
        return abilities._best_opp_char(g, p)
    if spec == "chosen_character":
        # your strongest ready character (heuristic; buffs help attackers)
        mine = [c for c in g.my_chars(p)]
        if not mine:
            return None
        return max(mine, key=lambda c: (not c.exerted, g.eff_strength(c)))
    if spec == "best_quester":
        # your highest-Lore character (heuristic for evasion/protection grants,
        # which protect a quester rather than pump an attacker). Mirrors the
        # Gyro-Evac TAKE HER UP choice in abilities.py.
        mine = [c for c in g.my_chars(p)]
        if not mine:
            return None
        return max(mine, key=lambda c: (g.eff_lore(c), g.eff_strength(c)))
    return ctx.get("char")


def _eff_stat_mod(g, p, ctx, eff):
    """target: self | chosen_opposing | chosen_character;
    stat: str | lore; duration: eot | until_your_next."""
    target = _resolve_target(g, p, ctx, eff.get("target", "self"))
    if target is None:
        return
    until = "eot" if eff.get("duration", "eot") == "eot" else p
    g.effects.append({"kind": eff.get("stat", "str"), "target": target.uid,
                      "amount": eff.get("amount", 1), "until": until})
    g.emit(f"schema: {target.card.base_name} {eff.get('amount'):+d} "
           f"{eff.get('stat', 'str')}")


def _eff_deal_damage(g, p, ctx, eff):
    tgt = _resolve_target(g, p, ctx, eff.get("target", "chosen_opposing"))
    if tgt is not None:
        g.deal_damage(tgt, eff.get("amount", 1))


def _eff_opponent_lose_lore(g, p, ctx, eff):
    opp = 1 - p
    amt = min(eff.get("amount", 1), g.players[opp].lore)
    if amt:
        g.players[opp].lore -= amt
        g.emit(f"schema: opponent loses {amt} lore")


def _eff_draw_then_discard(g, p, ctx, eff):
    """'Draw N cards, then choose and discard M cards.' The discard is
    mandatory and part of the same effect, so it must not be skipped even
    when the draw whiffs on an empty deck. Heuristic choice: _worst_hand_card,
    the same picker Strike A Good Match and EYE FOR VALUE use."""
    from . import abilities
    g.draw(p, eff.get("draw", 2))
    for _ in range(eff.get("discard", 1)):
        if not g.players[p].hand:
            break
        d = abilities._worst_hand_card(g, p)
        g.players[p].hand.remove(d)
        g.discard_card(p, d)
        g.emit(f"{ctx.get('card').base_name} (schema) discards {d.name}")


def _eff_grant_keyword(g, p, ctx, eff):
    """Grant a keyword to a target for a duration. Prose-granted keywords are
    deliberately not parsed as printed (see ASSUMPTIONS), so they are modeled
    as timed entries in g.effects, which has_evasive() and friends consult.

    duration: eot | until_your_next (start of your next turn).
    """
    target = _resolve_target(g, p, ctx, eff.get("target", "self"))
    if target is None:
        return
    kw = eff.get("keyword", "evasive")
    until = "eot" if eff.get("duration", "eot") == "eot" else p
    g.effects.append({"kind": kw, "target": target.uid,
                      "amount": 0, "until": until})
    g.emit(f"schema: {target.card.base_name} gains {kw}")


def _eff_opponent_discard(g, p, ctx, eff):
    from . import abilities
    opp = 1 - p
    for _ in range(eff.get("amount", 1)):
        if g.players[opp].hand:
            c = abilities._worst_hand_card(g, opp)
            g.players[opp].hand.remove(c)
            g.players[opp].discard.append(c)
            g.emit(f"schema: opponent discards {c.name}")


_EFFECTS = {
    "draw": _eff_draw,
    "gain_lore": _eff_gain_lore,
    "gain_lore_equal_to_exerted": _eff_gain_lore_equal_to_exerted,
    "cost_reduce": _eff_cost_reduce,
    "stat_mod": _eff_stat_mod,
    "deal_damage": _eff_deal_damage,
    "draw_then_discard": _eff_draw_then_discard,
    "grant_keyword": _eff_grant_keyword,
    "opponent_lose_lore": _eff_opponent_lose_lore,
    "opponent_discard": _eff_opponent_discard,
}


def apply_effect(g, p, ctx, eff):
    fn = _EFFECTS.get(eff.get("type"))
    if fn is None:
        g.emit(f"schema: unknown effect {eff.get('type')} (skipped)")
        return
    fn(g, p, ctx, eff)


# ---------------------------------------------------------------------
# Dispatchers, called from abilities.py trigger hooks
# ---------------------------------------------------------------------
def _run(g, p, ctx, ents):
    for e in ents:
        if "effect" not in e:
            continue  # e.g. {"impl": "python"} marker entries
        if check_condition(g, p, ctx, e.get("condition")):
            apply_effect(g, p, ctx, e["effect"])
            if g.winner is not None:
                return


def dispatch_play(g, p, card, obj, params):
    ents = entries_for(card.name, "on_play")
    if ents:
        _run(g, p, {"card": card, "char": obj if card.is_character else None}, ents)


def dispatch_quest(g, ch):
    ents = entries_for(ch.card.name, "on_quest")
    if ents:
        _run(g, ch.owner, {"card": ch.card, "char": ch}, ents)


# ---------------------------------------------------------------------
# Static (continuous) abilities: read by derived-stat functions, not
# dispatched at an event. Returns the numeric contribution or 0.
# ---------------------------------------------------------------------
def static_self_lore(g, ch):
    total = 0
    for e in entries_for(ch.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") == "static_self_lore":
            if check_condition(g, ch.owner, {"card": ch.card, "char": ch},
                               e.get("condition")):
                total += eff.get("amount", 0)
    return total


def static_location_resist(g, loc):
    """Sum of 'your locations gain Resist +N' from your characters in play."""
    total = 0
    owner = loc.owner
    for c in g.my_chars(owner):
        for e in entries_for(c.card.name, "static"):
            eff = e.get("effect", {})
            if eff.get("type") == "static_location_resist":
                total += eff.get("amount", 0)
    return total
