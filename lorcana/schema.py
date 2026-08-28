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
  triggers:   on_play, on_quest, activated, static
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
    filt = eff.get("filter")
    if filt:
        from . import abilities
        tgt = abilities._best_opp_char(
            g, p, cond=lambda gg, c: _char_matches(gg, c, filt))
    else:
        tgt = _resolve_target(g, p, ctx, eff.get("target", "chosen_opposing"))
    if tgt is not None:
        g.deal_damage(tgt, eff.get("amount", 1),
                      apply_resist=not eff.get("ignore_resist", False))


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


# ---------------------------------------------------------------------
# Additional effects (Phase 4). Each takes (g, p, ctx, eff).
# ctx may carry "banished_cost" from a banish_own_char activation cost.
# ---------------------------------------------------------------------
def _card_matches(card, filt):
    """Match a Card object against a filter dict."""
    if not filt:
        return True
    ct = filt.get("card_type")
    if ct == "character" and not card.is_character:
        return False
    if ct == "item" and not card.is_item:
        return False
    if ct == "action" and not card.is_action:
        return False
    if ct == "non_character" and card.is_character:
        return False
    if filt.get("max_cost") is not None and card.cost > filt["max_cost"]:
        return False
    if filt.get("name") and card.base_name != filt["name"]:
        return False
    return True


def _char_matches(g, ch, filt):
    """Match a CharInPlay against a filter dict."""
    if not filt:
        return True
    if filt.get("damaged") and ch.damage <= 0:
        return False
    if filt.get("max_strength") is not None \
            and g.eff_strength(ch) > filt["max_strength"]:
        return False
    if filt.get("max_cost") is not None and ch.card.cost > filt["max_cost"]:
        return False
    if filt.get("classification") \
            and filt["classification"] not in ch.card.classifications:
        return False
    return True


def _eff_look_at_top(g, p, ctx, eff):
    """Look at the top N cards; put the best match into hand (or reveal it),
    put the rest on the bottom. Deck top is the END of the list."""
    pl = g.players[p]
    n = eff.get("count", 3)
    if not pl.deck:
        return
    n = min(n, len(pl.deck))
    looked = [pl.deck.pop() for _ in range(n)]        # index 0 == topmost
    filt = eff.get("filter")
    taken = None
    if eff.get("destination", "hand") == "hand":
        matches = [c for c in looked if _card_matches(c, filt)]
        if matches:
            # heuristic: take the most expensive legal hit (best card)
            taken = max(matches, key=lambda c: c.cost)
            looked.remove(taken)
            pl.hand.append(taken)
    # rest to the bottom of the deck (front of the list) in any order
    for c in reversed(looked):
        pl.deck.insert(0, c)
    g.emit(f"schema: looked at top {n}, "
           + (f"took {taken.name}" if taken else "took nothing"))


def _eff_banish_all(g, p, ctx, eff):
    """Banish every character on a side matching a filter."""
    side = eff.get("side", "opposing")
    filt = eff.get("filter")
    owners = {"opposing": [1 - p], "yours": [p], "all": [p, 1 - p]}[side]
    victims = [c for c in g.chars.values()
               if c.owner in owners and _char_matches(g, c, filt)]
    for c in victims:
        g.emit(f"schema: banishes {c.card.base_name}(P{c.owner})")
        g.banish_char(c, cause="effect")
        if g.winner is not None:
            return


def _eff_return_to_hand(g, p, ctx, eff):
    """Return a chosen character to its owner's hand."""
    from . import abilities
    filt = eff.get("filter")
    side = eff.get("side", "opposing")
    if side == "opposing":
        tgt = abilities._best_opp_char(
            g, p, cond=lambda gg, c: _char_matches(gg, c, filt))
    else:
        pool = [c for c in g.my_chars(p) if _char_matches(g, c, filt)]
        tgt = min(pool, key=lambda c: (g.eff_lore(c), g.eff_strength(c))) \
            if pool else None
    if tgt is None:
        return
    g.chars.pop(tgt.uid, None)
    g.players[tgt.owner].hand.append(tgt.card)
    g.emit(f"schema: returns {tgt.card.base_name}(P{tgt.owner}) to hand")


def _eff_play_from_discard(g, p, ctx, eff):
    """Play a card from your discard for free."""
    pl = g.players[p]
    filt = eff.get("filter") or {"card_type": "character"}
    pool = [c for c in pl.discard if _card_matches(c, filt)]
    if not pool:
        return
    pick = max(pool, key=lambda c: c.cost)
    pl.discard.remove(pick)
    g.emit(f"schema: plays {pick.name} from discard (free)")
    g._play_card(p, pick, {}, free=True)


def _eff_play_from_hand_free(g, p, ctx, eff):
    """Play a card from hand for free, optionally capped by a cost ceiling.
    'max_cost_delta' is relative to ctx['banished_cost'] when present."""
    pl = g.players[p]
    filt = dict(eff.get("filter") or {"card_type": "character"})
    cap = eff.get("max_cost")
    if eff.get("max_cost_delta") is not None:
        base = ctx.get("banished_cost")
        if base is None:
            return
        cap = base + eff["max_cost_delta"]
    if cap is not None:
        filt["max_cost"] = cap if filt.get("max_cost") is None \
            else min(cap, filt["max_cost"])
    pool = [c for c in pl.hand if _card_matches(c, filt)]
    if not pool:
        return
    pick = max(pool, key=lambda c: c.cost)
    g.emit(f"schema: plays {pick.name} from hand (free)")
    g._play_card(p, pick, {}, free=True)


_EFFECTS = {
    "look_at_top": _eff_look_at_top,
    "banish_all": _eff_banish_all,
    "return_to_hand": _eff_return_to_hand,
    "play_from_discard": _eff_play_from_discard,
    "play_from_hand_free": _eff_play_from_hand_free,
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
        if not check_condition(g, p, ctx, e.get("condition")):
            continue
        # Optional cost on a triggered ability ("you may pay 2 Ink to ...").
        # Skipped rather than failed when unaffordable, matching "you may".
        cost = e.get("cost")
        if cost:
            pl = g.players[p]
            if pl.ink_ready < cost.get("ink", 0):
                continue
            if cost.get("discard", 0) > len(pl.hand):
                continue
            if cost.get("ink", 0):
                g.pay_ink(p, cost["ink"])
            for _ in range(cost.get("discard", 0)):
                from . import abilities
                card = abilities._worst_hand_card(g, p)
                if card is None:
                    break
                pl.hand.remove(card)
                pl.discard.append(card)
                g.emit(f"schema: discards {card.name} (cost)")
        apply_effect(g, p, ctx, e["effect"])
        if g.winner is not None:
            return


def dispatch_play(g, p, card, obj, params):
    ents = entries_for(card.name, "on_play")
    if ents:
        _run(g, p, {"card": card, "char": obj if card.is_character else None}, ents)


# ---------------------------------------------------------------------
# Activated abilities (Phase 4).
#
# The engine already has the plumbing: abilities.activated_actions() puts
# ("activate", key, uid) tuples into the action space and engine.apply()
# routes "activate" to abilities.apply_activated(). Everything there was
# hand-written per card. These helpers let abilities_manual/auto express the
# same thing as data, so functional reprints stop needing new Python.
#
# Entry shape:
#   {"trigger": "activated",
#    "cost": {"exert": true, "ink": 1, "banish_self": false,
#             "banish_own_char": false, "discard": 0},
#    "effect": {...}}
# ---------------------------------------------------------------------
def activated_entries(card_name):
    return entries_for(card_name, "activated")


def _obj_is_char(obj):
    return hasattr(obj, "damage")


def can_activate(g, p, obj, entry):
    """Is this activated ability legally available right now?"""
    cost = entry.get("cost") or {}
    pl = g.players[p]
    if cost.get("exert", True):
        if getattr(obj, "exerted", False):
            return False
        # characters need to be dry; items and locations do not
        if _obj_is_char(obj) and not g.is_dry(obj):
            return False
    if pl.ink_ready < cost.get("ink", 0):
        return False
    if cost.get("discard", 0) > len(pl.hand):
        return False
    if cost.get("banish_own_char"):
        others = [c for c in g.my_chars(p)
                  if not (_obj_is_char(obj) and c.uid == obj.uid)]
        if not others:
            return False
    return True


def _pay_activation_cost(g, p, obj, entry, ctx):
    """Pay the cost. Returns False if it could not be paid."""
    from . import abilities
    cost = entry.get("cost") or {}
    pl = g.players[p]
    if not can_activate(g, p, obj, entry):
        return False
    if cost.get("exert", True):
        obj.exerted = True
    if cost.get("ink", 0):
        g.pay_ink(p, cost["ink"])
    for _ in range(cost.get("discard", 0)):
        if not pl.hand:
            break
        card = abilities._worst_hand_card(g, p)
        pl.hand.remove(card)
        pl.discard.append(card)
        g.emit(f"schema: discards {card.name} (cost)")
    if cost.get("banish_own_char"):
        others = [c for c in g.my_chars(p)
                  if not (_obj_is_char(obj) and c.uid == obj.uid)]
        if others:
            # cheapest body; record its cost for cost-relative effects
            victim = min(others, key=lambda c: (g.eff_lore(c), c.card.cost))
            ctx["banished_cost"] = victim.card.cost
            g.emit(f"schema: banishes own {victim.card.base_name} (cost)")
            g.banish_char(victim, cause="effect")
    if cost.get("banish_self"):
        if _obj_is_char(obj):
            g.banish_char(obj, cause="effect")
        elif obj in g.items[p]:
            g.banish_item(obj)
    return True


def dispatch_activated(g, p, obj, index):
    """Run the index-th activated entry on obj's card."""
    ents = activated_entries(obj.card.name)
    if index >= len(ents):
        return
    entry = ents[index]
    ctx = {"card": obj.card, "char": obj if _obj_is_char(obj) else None}
    if not _pay_activation_cost(g, p, obj, entry, ctx):
        return
    if check_condition(g, p, ctx, entry.get("condition")):
        apply_effect(g, p, ctx, entry["effect"])


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
