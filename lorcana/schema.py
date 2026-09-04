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
  triggers:   on_play, on_play_character, on_shift, on_quest, on_banish,
              on_action_damage, on_opposing_challenge,
              on_chosen_by_opponent, on_play_location, on_play_action,
              on_challenged, on_ally_challenged, on_leave_play,
              activated, static
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


_ENTRIES_CACHE = {}
_EMPTY = ()


def entries_for(card_name, trigger):
    # A card implemented in Python must never also run schema entries, or its
    # effects would double-apply. Manual entries are exempt (they are authored
    # deliberately and are expected to be the single source for that card).
    #
    # Pure function of (card_name, trigger) over immutable JSON, so memoized:
    # this was rebuilding a list several million times per search. The returned
    # sequence is SHARED -- callers must treat it as read-only.
    key = (card_name, trigger)
    hit = _ENTRIES_CACHE.get(key)
    if hit is not None:
        return hit
    if card_name in _hand_implemented() and card_name not in registry_manual_names():
        out = _EMPTY
    else:
        ents = registry().get(card_name) or _EMPTY
        out = [e for e in ents
               if isinstance(e, dict) and e.get("trigger") == trigger] or _EMPTY
    _ENTRIES_CACHE[key] = out
    return out


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


def _cond_cards_played_this_turn(g, p, ctx, cond):
    """You've played N or more cards this turn (Enigmatic Inkcaster)."""
    return g.cards_played[p] >= cond.get("count", 2)


def _cond_named_banished_this_turn(g, p, ctx, cond):
    """A character with this base name was banished this turn, either side
    (Buzz's Arm MISSING PIECE)."""
    return ("banished_base", cond.get("name")) in g.turn_flags


def _cond_inkwell_all_exerted(g, p, ctx, cond):
    """All cards in your inkwell are exerted (Randall Boggs). ink_ready is the
    unexerted count, so this is simply having spent everything."""
    return g.players[p].ink_total > 0 and g.players[p].ink_ready == 0


def _cond_has_card_under(g, p, ctx, cond):
    """There is a card under this character, from Boost or from being shifted
    onto (Ursula - Whisper of Vanessa)."""
    ch = ctx.get("char")
    if ch is None:
        return False
    return bool(getattr(ch, "boosted", None) or getattr(ch, "under", None))


def _cond_you_have_keyword(g, p, ctx, cond):
    """You control a character with the named keyword (Vixey)."""
    kw = cond.get("keyword", "evasive").lower()
    fn = {"evasive": abilities.has_evasive,
          "reckless": abilities.has_reckless,
          "ward": abilities.has_ward,
          "support": abilities.has_support}.get(kw)
    if fn is None:
        # Keywords with no helper (Singer, Bodyguard, Challenger, ...) are
        # read straight off the printed card. Returning False here instead
        # would make the card report [ok] and silently never fire.
        printed = {"singer": "Singer", "bodyguard": "Bodyguard",
                   "challenger": "Challenger", "rush": "Rush",
                   "resist": "Resist", "shift": "Shift"}.get(kw)
        if printed is None:
            return False
        return any(c.card.kw(printed) for c in g.my_chars(p))
    return any(fn(g, c) for c in g.my_chars(p))


def _cond_damage_to_move(g, p, ctx, cond):
    """The move_damage ability would actually do something: either one of your
    other characters is damaged, or this one is already at the dump threshold.
    Without this the ink-only activation is offered every turn and MCTS burns
    ink exploring a no-op."""
    me = ctx.get("char")
    if me is None:
        return False
    if any(c.uid != me.uid and c.damage > 0 for c in g.my_chars(p)):
        return True
    thresh = cond.get("dump_at")
    return thresh is not None and me.damage >= thresh \
        and any(True for _ in g.my_chars(1 - p))


def _cond_damage_would_banish(g, p, ctx, cond):
    """N damage would finish off at least one opposing character.

    Used to gate one-shot removal whose cost is banishing its own source
    (The Robot Queen). Firing it on the first character played each game
    would usually waste the item, so it is held until it actually trades up.
    Resist is subtracted, since it reduces the damage that lands.
    """
    n = cond.get("amount", 1)
    for c in g.my_chars(1 - p):
        if abilities.has_ward(g, c):
            continue
        landed = max(0, n - g.eff_resist(c))
        if landed and g.eff_willpower(c) - c.damage <= landed:
            return True
    return False


def _cond_you_have_classification(g, p, ctx, cond):
    """You control a character with any of the named classifications.

    The banished character is already out of g.chars when on_banish fires
    (engine.banish_char deletes at :302 and dispatches at :308), so a card
    like Sleepy - Deep Sleeper, who is himself a Seven Dwarf, correctly does
    not satisfy his own condition.
    """
    want = cond.get("any_of") or [cond.get("name")]
    want = {w for w in want if w}
    return any(c.card.classifications & want for c in g.my_chars(p))


def _cond_first_turn_on_the_draw(g, p, ctx, cond):
    """It is your first turn and you were not the first player.

    g.turn is a global half-turn counter incremented once per player turn,
    and player 0 always starts (engine.Game sets active = 0 and start() does
    not flip it), so the second player's first turn is exactly turn 2.
    """
    return p == 1 and g.turn == 2


def _cond_self_undamaged(g, p, ctx, cond):
    ch = ctx.get("char")
    return ch is not None and ch.damage == 0


def _cond_classification_in_play(g, p, ctx, cond):
    """A character with this classification is in play, on either side
    (Incrediboy NERDING OUT says 'is in play', not 'you have')."""
    want = set(cond.get("any_of") or [cond.get("name")]) - {None}
    return any(c.card.classifications & want for c in g.chars.values())


def _cond_played_via_shift(g, p, ctx, cond):
    """This character was played by shifting onto another (Omnidroid V.9)."""
    return bool((ctx.get("params") or {}).get("shift"))


def _cond_hand_size_at_least(g, p, ctx, cond):
    """You have N or more cards in hand (Demona STONE BY DAY)."""
    return len(g.players[p].hand) >= cond.get("count", 1)


def _cond_discarded_this_turn(g, p, ctx, cond):
    """You discarded a card this turn (Discarded Armor FOUND EQUIPMENT).
    engine.discard_card already keeps this count for Milo."""
    return g.turn_discards.get(p, 0) > 0


def _cond_opposing_damaged_present(g, p, ctx, cond):
    """There is a choosable damaged opposing character. Gates abilities whose
    cost is paid up front (Lord MacGuffin enters exerted) so the cost is never
    paid for nothing."""
    from . import abilities
    return abilities._best_opp_char(
        g, p, cond=lambda gg, c: c.damage > 0, notify=False) is not None


def _cond_opponent_has_more_lore(g, p, ctx, cond):
    return g.players[1 - p].lore > g.players[p].lore


def _cond_opposing_damaged_in_play(g, p, ctx, cond):
    """An opposing damaged character is in play. Unlike
    opposing_damaged_present this does not care about Ward, because it is a
    static check rather than a choice (The Queen - Evil Ruler)."""
    return any(c.damage > 0 for c in g.my_chars(1 - p))


def _cond_discards_this_turn_at_least(g, p, ctx, cond):
    """N or more cards were put into your discard this turn (Helga
    Sinclair). engine.turn_discards already counts banishes and discards."""
    return g.turn_discards.get(p, 0) >= cond.get("count", 1)


def _cond_played_another_character(g, p, ctx, cond):
    """You played another character this turn (Donald Duck - Distracted
    Traveler). The engine records this flag after each character resolves."""
    return ("played_char", p) in g.turn_flags


def _cond_named_character_in_play(g, p, ctx, cond):
    return any(c.card.base_name == cond.get("name") for c in g.my_chars(p))


def _cond_opponents_turn(g, p, ctx, cond):
    """It is not your turn (Yao - Snow Warrior)."""
    return g.active != p


def _cond_character_here(g, p, ctx, cond):
    """You have a character at this location (Paradise Falls)."""
    loc = ctx.get("loc")
    if loc is None:
        return False
    return any(c.location == loc.uid for c in g.my_chars(p))


def _cond_permanent_with_card_under(g, p, ctx, cond):
    """You control a character or location with a card under it
    (Flintheart Glomgold TRY ME)."""
    for c in g.my_chars(p):
        if getattr(c, "under", None) or getattr(c, "boosted", None):
            return True
    for l in g.my_locs(p):
        if getattr(l, "under", None) or getattr(l, "boosted", None):
            return True
    return False


def _cond_self_at_location(g, p, ctx, cond):
    """This character is at a location (Shenzi I'LL HANDLE THIS)."""
    ch = ctx.get("char")
    return ch is not None and ch.location is not None


def _cond_being_challenged(g, p, ctx, cond):
    """This character is the defender in the challenge being resolved
    (Enchantress TRUE FORM). engine._challenge sets challenge_ctx."""
    ch = ctx.get("char")
    cc = getattr(g, "challenge_ctx", None)
    return ch is not None and cc is not None and cc[1] == ch.uid


def _cond_self_exerted(g, p, ctx, cond):
    ch = ctx.get("char")
    return ch is not None and ch.exerted


def _cond_self_damaged(g, p, ctx, cond):
    ch = ctx.get("char")
    return ch is not None and ch.damage > 0


def _cond_your_turn(g, p, ctx, cond):
    return g.active == p


def _cond_all_of(g, p, ctx, cond):
    """Every sub-condition must hold (Milo Thatch: your turn AND 2+ discards)."""
    return all(check_condition(g, p, ctx, c) for c in cond.get("all_of") or [])


def _cond_classification_banished_this_turn(g, p, ctx, cond):
    """A character with this classification was banished this turn
    (Wind-Up Frog ADDED TRACTION). engine.on_banish records the tags."""
    return ("banished_class", cond.get("name")) in g.turn_flags


def _cond_you_have_damaged_character(g, p, ctx, cond):
    return any(c.damage > 0 for c in g.my_chars(p))


def _cond_others_with_strength(g, p, ctx, cond):
    """N or more OTHER characters of yours with Strength >= X
    (Elisa Maza - Intrepid Investigator)."""
    me = ctx.get("char")
    n = sum(1 for c in g.my_chars(p)
            if (me is None or c.uid != me.uid)
            and g.eff_strength(c) >= cond.get("strength", 1))
    return n >= cond.get("count", 1)


def _cond_keyword_character_here(g, p, ctx, cond):
    """A character with this keyword is at this location (Game Preserve)."""
    from . import abilities
    loc = ctx.get("loc")
    if loc is None:
        return False
    fn = {"evasive": abilities.has_evasive, "ward": abilities.has_ward,
          "reckless": abilities.has_reckless}.get(
              cond.get("keyword", "evasive").lower())
    if fn is None:
        return False
    return any(c.location == loc.uid and fn(g, c) for c in g.chars.values())


def _cond_put_card_under_this_turn(g, p, ctx, cond):
    """You put a card under something this turn. "self" scopes it to this
    character (Willie the Giant, Lady Tremaine); otherwise any of yours
    (Mulan - Standing Her Ground)."""
    if cond.get("scope") == "self":
        ch = ctx.get("char") or ctx.get("source")
        if ch is None:
            return False
        return ("under_this_turn", ch.uid) in g.turn_flags
    return ("under_this_turn", p) in g.turn_flags


def _cond_character_banished_this_turn(g, p, ctx, cond):
    """Any character was banished this turn, either side
    (Mother Gothel - Underhanded Schemer)."""
    return ("banished_this_turn",) in g.turn_flags


def _cond_opponent_more_inkwell(g, p, ctx, cond):
    """An opponent has more cards in their inkwell than you
    (Heihei, Webby Vanderquack)."""
    return g.players[1 - p].ink_total > g.players[p].ink_total


def _cond_song_played_this_turn(g, p, ctx, cond):
    """You played a song this turn (Powerline - Musical Superstar)."""
    return ("song_played", p) in g.turn_flags


def _cond_opponent_lore_at_most(g, p, ctx, cond):
    return g.players[1 - p].lore <= cond.get("amount", 0)


def _cond_opponent_ready_characters(g, p, ctx, cond):
    """The opponent has N or more ready (unexerted) characters in play."""
    n = sum(1 for c in g.my_chars(1 - p) if not c.exerted)
    return n >= cond.get("count", 1)


def _cond_banished_in_challenge(g, p, ctx, cond):
    """A character was banished in a challenge this turn. "opposing" scopes
    it to the other player's characters (Card Advantage, Chief)."""
    if cond.get("side") == "opposing":
        return ("chal_banish", 1 - p) in g.turn_flags
    return any(("chal_banish", who) in g.turn_flags for who in (0, 1))


def _cond_hand_empty(g, p, ctx, cond):
    return not g.players[p].hand


def _cond_no_named_character(g, p, ctx, cond):
    """You do NOT control a character with this name (Launchpad's 'unless')."""
    return not any(c.card.base_name == cond.get("name")
                   for c in g.my_chars(p))


_CONDITIONS = {
    "hand_empty": _cond_hand_empty,
    "no_named_character": _cond_no_named_character,
    "banished_in_challenge_this_turn": _cond_banished_in_challenge,
    "opponent_lore_at_most": _cond_opponent_lore_at_most,
    "opponent_ready_characters": _cond_opponent_ready_characters,
    "song_played_this_turn": _cond_song_played_this_turn,
    "opponent_more_inkwell": _cond_opponent_more_inkwell,
    "character_banished_this_turn": _cond_character_banished_this_turn,
    "put_card_under_this_turn": _cond_put_card_under_this_turn,
    "keyword_character_here": _cond_keyword_character_here,
    "you_have_damaged_character": _cond_you_have_damaged_character,
    "others_with_strength": _cond_others_with_strength,
    "all_of": _cond_all_of,
    "classification_banished_this_turn": _cond_classification_banished_this_turn,
    "self_damaged": _cond_self_damaged,
    "your_turn": _cond_your_turn,
    "self_at_location": _cond_self_at_location,
    "being_challenged": _cond_being_challenged,
    "self_exerted": _cond_self_exerted,
    "character_here": _cond_character_here,
    "permanent_with_card_under": _cond_permanent_with_card_under,
    "opponent_has_more_lore": _cond_opponent_has_more_lore,
    "opposing_damaged_in_play": _cond_opposing_damaged_in_play,
    "discards_this_turn_at_least": _cond_discards_this_turn_at_least,
    "played_another_character": _cond_played_another_character,
    "named_character_in_play": _cond_named_character_in_play,
    "opponents_turn": _cond_opponents_turn,
    "discarded_this_turn": _cond_discarded_this_turn,
    "opposing_damaged_present": _cond_opposing_damaged_present,
    "hand_size_at_least": _cond_hand_size_at_least,
    "self_undamaged": _cond_self_undamaged,
    "classification_in_play": _cond_classification_in_play,
    "played_via_shift": _cond_played_via_shift,
    "you_have_classification": _cond_you_have_classification,
    "first_turn_on_the_draw": _cond_first_turn_on_the_draw,
    "damage_would_banish": _cond_damage_would_banish,
    "damage_to_move": _cond_damage_to_move,
    "inkwell_all_exerted": _cond_inkwell_all_exerted,
    "has_card_under": _cond_has_card_under,
    "you_have_keyword": _cond_you_have_keyword,
    "cards_played_this_turn": _cond_cards_played_this_turn,
    "named_banished_this_turn": _cond_named_banished_this_turn,
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
    # "another chosen character" must differ from one already hit by an
    # earlier clause of the same ability (Three Arrows).
    seen = ctx.get("damaged_uids") or set()
    excl = eff.get("exclude_previous")
    if filt or excl:
        def _ok(gg, c):
            if excl and c.uid in seen:
                return False
            return _char_matches(gg, c, filt) if filt else True
        tgt = abilities._best_opp_char(g, p, cond=_ok)
    else:
        tgt = _resolve_target(g, p, ctx, eff.get("target", "chosen_opposing"))
    if tgt is not None:
        ctx.setdefault("damaged_uids", set()).add(tgt.uid)
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
    filt = eff.get("target_filter")
    if filt:
        # "chosen <classification> character" -- your own board, best quester
        pool = [c for c in g.my_chars(p)
                if filt.get("classification") in c.card.classifications]
        target = max(pool, key=lambda c: (g.eff_lore(c), g.eff_strength(c))) \
            if pool else None
    else:
        target = _resolve_target(g, p, ctx, eff.get("target", "self"))
    if target is None:
        return
    kw = eff.get("keyword", "evasive")
    until = "eot" if eff.get("duration", "eot") == "eot" else p
    # Numeric keywords (Challenger +N, Resist +N) carry their value here;
    # challenger_bonus() and resist() sum g.effects entries by amount, so a
    # grant with amount 0 would be silently inert.
    g.effects.append({"kind": kw, "target": target.uid,
                      "amount": eff.get("amount", 0), "until": until})
    g.emit(f"schema: {target.card.base_name} gains {kw}"
           + (f" +{eff['amount']}" if eff.get("amount") else ""))


def _eff_opponent_discard(g, p, ctx, eff):
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
    """Match a Card object against a filter dict.

    "any_of" holds a list of sub-filters, any one of which is enough. My
    Adventure Book needs it: a non-character card OR a character named Kevin.
    """
    if not filt:
        return True
    if filt.get("any_of"):
        return any(_card_matches(card, f) for f in filt["any_of"])
    ct = filt.get("card_type")
    if ct == "character" and not card.is_character:
        return False
    if ct == "item" and not card.is_item:
        return False
    if ct == "action" and not card.is_action:
        return False
    if ct == "song" and not getattr(card, "is_song", False):
        return False
    if ct == "location" and not card.is_location:
        return False
    if ct == "non_character" and card.is_character:
        return False
    if filt.get("max_cost") is not None and card.cost > filt["max_cost"]:
        return False
    if filt.get("name") and card.base_name != filt["name"]:
        return False
    if filt.get("classification") \
            and filt["classification"] not in card.classifications:
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
    """Return a chosen character to its owner's hand.

    With zones=["character","item","location"] the choice widens to any
    permanent matching the filter (Vixey STEALING IN). Items and locations
    have no lore or strength to rank by, so cost is the tiebreak.
    """
    filt = eff.get("filter")
    side = eff.get("side", "opposing")
    zones = eff.get("zones")
    if zones and zones != ["character"]:
        return _return_permanent(g, p, eff, filt, side, zones)
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


def _return_permanent(g, p, eff, filt, side, zones):
    owners = [1 - p] if side == "opposing" else [p]
    cands = []
    if "character" in zones:
        for o in owners:
            cands += [(c, "char") for c in g.my_chars(o)
                      if _char_matches(g, c, filt)
                      and not (o != p and abilities.has_ward(g, c))]
    if "item" in zones:
        for o in owners:
            cands += [(i, "item") for i in g.items[o]
                      if _card_matches(i.card, filt)]
    if "location" in zones:
        for o in owners:
            cands += [(l, "loc") for l in g.my_locs(o)
                      if _card_matches(l.card, filt)]
    if not cands:
        return
    obj, kind = max(cands, key=lambda t: t[0].card.cost)
    owner = obj.owner
    if kind == "char":
        g.chars.pop(obj.uid, None)
    elif kind == "item":
        if obj in g.items[owner]:
            g.items[owner].remove(obj)
    else:
        g.locs.pop(obj.uid, None)
    g.players[owner].hand.append(obj.card)
    g.emit(f"schema: returns {obj.card.base_name}(P{owner}) to hand")


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



def _eff_mass_grant_keyword(g, p, ctx, eff):
    """Grant a keyword to every character on a side matching a filter
    (Potion of Malice MINDLESS RAGE)."""
    side = eff.get("side", "opposing")
    owners = {"opposing": [1 - p], "yours": [p], "all": [p, 1 - p]}[side]
    kw = eff.get("keyword", "reckless")
    until = "eot" if eff.get("duration", "eot") == "eot" else p
    n = 0
    for c in list(g.chars.values()):
        if c.owner in owners and _char_matches(g, c, eff.get("filter")):
            g.effects.append({"kind": kw, "target": c.uid,
                              "amount": 0, "until": until})
            n += 1
    g.emit(f"schema: {n} character(s) gain {kw}")


def _eff_quest_lock(g, p, ctx, eff):
    """Up to N chosen characters can't quest until the start of your next
    turn (Strange Things). Modeled as a timed effect rather than a turn flag
    because the lock has to survive the opponent's turn."""
    n = eff.get("count", 1)
    until = "eot" if eff.get("duration") == "eot" else p
    picked = []
    for _ in range(n):
        tgt = abilities._best_opp_char(
            g, p, cond=lambda gg, c: c.uid not in [x.uid for x in picked]
            and _char_matches(gg, c, eff.get("filter")))
        if tgt is None:
            break                       # "up to": fewer targets is legal
        picked.append(tgt)
        g.effects.append({"kind": "no_quest", "target": tgt.uid,
                          "amount": 0, "until": until})
    if picked:
        g.emit("schema: quest-locks "
               + ", ".join(c.card.base_name for c in picked))


def _eff_banish_location(g, p, ctx, eff):
    """Banish chosen opposing location (Battering Ram BREAK THROUGH)."""
    opp = 1 - p
    locs = list(g.my_locs(opp))
    if not locs:
        return
    tgt = max(locs, key=lambda l: (g.loc_lore(l), l.card.cost))
    g.emit(f"schema: banishes location {tgt.card.base_name}")
    g.banish_loc(tgt)


def _eff_opponent_scatter(g, p, ctx, eff):
    """Chosen opponent picks 3 of their characters: one to hand, one to the
    bottom of their deck, one to the top (The Family Scattered).

    The opponent chooses, so the heuristic is theirs, not ours: they keep the
    best body (top of deck, drawn next turn), take the middle one back to
    hand, and bury the worst. With fewer than 3 characters, everything they
    have is scattered, cheapest destination first.
    """
    opp = 1 - p
    mine = list(g.my_chars(opp))
    if not mine:
        return
    ranked = sorted(mine, key=lambda c: (g.eff_lore(c), g.eff_strength(c),
                                         c.card.cost), reverse=True)
    picks = ranked[:3]
    dests = ["top", "hand", "bottom"][:len(picks)]
    for c, dest in zip(picks, dests):
        g.chars.pop(c.uid, None)
        if dest == "hand":
            g.players[opp].hand.append(c.card)
        elif dest == "top":
            g.players[opp].deck.append(c.card)      # top of deck == end
        else:
            g.players[opp].deck.insert(0, c.card)
        g.emit(f"schema: {c.card.base_name}(P{opp}) -> {dest}")


def alt_cost_available(g, p, card):
    """Is an alternate 'put a card from your discard on the bottom to play
    this for free' cost payable right now (Hand-in-the-Box)?"""
    for e in entries_for(card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") != "play_free_via_bottom":
            continue
        pool = [c for c in g.players[p].discard
                if _card_matches(c, eff.get("filter"))]
        if len(pool) >= eff.get("count", 1):
            return eff
    return None


def pay_alt_cost(g, p, card, eff):
    pl = g.players[p]
    pool = [c for c in pl.discard if _card_matches(c, eff.get("filter"))]
    for c in pool[:eff.get("count", 1)]:
        pl.discard.remove(c)
        pl.deck.insert(0, c)
    g.emit(f"schema: bottoms {eff.get('count', 1)} card(s) to play "
           f"{card.name} free")


def static_free_discount(g, p, card):
    """Conditional self-discounts on playing this card, expressed as static
    entries so the existing play_cost / static_discount path handles them.

    "play_free_if" waives the whole cost; "play_cost_reduction" takes a fixed
    amount off (Christopher Robin UNDERDOG). Returns the total ink discount.
    """
    total = 0
    ctx = {"card": card, "char": None}
    for e in entries_for(card.name, "static"):
        t = e.get("effect", {}).get("type")
        if t not in ("play_free_if", "play_cost_reduction"):
            continue
        if not check_condition(g, p, ctx, e.get("condition")):
            continue
        if t == "play_free_if":
            total += card.cost
            continue
        amount = e["effect"].get("amount", 1)
        per = e["effect"].get("per")
        if per == "classification_in_discard":
            want = e["effect"].get("classification")
            amount *= sum(1 for c in g.players[p].discard
                          if want in c.classifications)
        elif per == "card_type_in_discard":
            want = {"card_type": e["effect"].get("card_type")}
            amount *= sum(1 for c in g.players[p].discard
                          if _card_matches(c, want))
        total += amount
    return min(total, card.cost)



def _eff_draw_then_discard(g, p, ctx, eff):
    """Draw N, then choose and discard N (Violet Parr HEROIC SYNERGY).
    Drawing first matters: the new card is a legal discard."""
    from . import abilities
    n = eff.get("amount", 1)
    pl = g.players[p]
    g.draw(p, n)
    for _ in range(eff.get("discard", n)):
        if not pl.hand:
            break
        card = abilities._worst_hand_card(g, p)
        if card is None:
            break
        pl.hand.remove(card)
        pl.discard.append(card)
        g.emit(f"schema: discards {card.name}")


def _eff_return_from_discard(g, p, ctx, eff):
    """Return a matching card from your discard to your hand."""
    pl = g.players[p]
    filt = eff.get("filter") or {}
    pool = [c for c in pl.discard if _card_matches(c, filt)]
    if not pool:
        return
    pick = max(pool, key=lambda c: c.cost)
    pl.discard.remove(pick)
    pl.hand.append(pick)
    g.emit(f"schema: returns {pick.name} from discard to hand")


def _eff_return_cards_under(g, p, ctx, eff):
    """Return every card under this character to its owner's hand
    (Omnidroid - Ultimate Iteration RETURN ON INVESTMENT)."""
    ch = ctx.get("char")
    if ch is None or not ch.under:
        return
    n = len(ch.under)
    g.players[ch.owner].hand.extend(ch.under)
    ch.under = []
    g.emit(f"schema: returns {n} card(s) from under "
           f"{ch.card.base_name} to hand")


def _eff_sequence(g, p, ctx, eff):
    """Apply several effects as one indivisible resolution. Used for
    activated abilities whose text is more than one sentence, so the whole
    ability stays a single action with a single cost."""
    for inner in eff.get("effects") or []:
        apply_effect(g, p, ctx, inner)
        if g.winner is not None:
            return


def _eff_put_top_into_inkwell(g, p, ctx, eff):
    """Put the top card of your deck into your inkwell facedown and exerted.
    The inkwell is modelled as two counters, so an exerted card raises the
    total without raising what is ready this turn."""
    pl = g.players[p]
    if not pl.deck:
        return
    pl.deck.pop()
    pl.ink_total += 1
    g.emit("schema: puts the top card into the inkwell (exerted)")


def _eff_exert_all_inkwell(g, p, ctx, eff):
    """Exert every card in your inkwell (Sulley, Scream Canister)."""
    pl = g.players[p]
    if pl.ink_ready:
        g.emit(f"schema: exerts {pl.ink_ready} inkwell card(s)")
        pl.ink_ready = 0


def _eff_discard_to_bottom(g, p, ctx, eff):
    """Put cards from your discard on the bottom of your deck. "count" caps
    how many; omit it (or pass "all") to move every match. Returns nothing,
    but records whether it moved the full amount so a follow-on effect can be
    gated (Roller Bob only gets Rush if he actually moved 2)."""
    pl = g.players[p]
    filt = eff.get("filter")
    pool = [c for c in pl.discard if _card_matches(c, filt)]
    n = len(pool) if eff.get("count") in (None, "all") else eff["count"]
    if eff.get("require_full") and len(pool) < n:
        ctx["_moved_full"] = False
        return
    moved = pool[:n]
    for c in moved:
        pl.discard.remove(c)
        pl.deck.insert(0, c)          # bottom of deck is the front
    ctx["_moved_full"] = len(moved) >= n and n > 0
    if moved:
        g.emit(f"schema: puts {len(moved)} card(s) from discard "
               f"on the bottom of the deck")


def _eff_then_if_moved(g, p, ctx, eff):
    """Run an inner effect only if the preceding discard_to_bottom moved its
    full amount."""
    if ctx.get("_moved_full"):
        apply_effect(g, p, ctx, eff["then"])


def _eff_discard_hand_then_return(g, p, ctx, eff):
    """Discard your hand, then return a card from your discard to your hand
    (Hercules - Young Rescuer HEROIC SACRIFICE). Only worth doing when the
    hand is small, so it is skipped above a threshold."""
    pl = g.players[p]
    if len(pl.hand) > eff.get("max_hand", 2):
        return
    n = len(pl.hand)
    pl.discard.extend(pl.hand)
    pl.hand.clear()
    g.emit(f"schema: discards {n} card(s) from hand")
    if pl.discard:
        pick = max(pl.discard, key=lambda c: c.cost)
        pl.discard.remove(pick)
        pl.hand.append(pick)
        g.emit(f"schema: returns {pick.name} to hand")


def _eff_opponent_banish_own(g, p, ctx, eff):
    """Each opponent chooses and banishes one of their own characters
    (Leviathan's Lair LOST TO THE DUNES). They choose, so they give up the
    least valuable body they control."""
    opp = 1 - p
    pool = list(g.my_chars(opp))
    if not pool:
        return
    victim = min(pool, key=lambda c: (g.eff_lore(c), g.eff_strength(c),
                                      c.card.cost))
    g.emit(f"schema: P{opp} banishes {victim.card.base_name}")
    g.banish_char(victim, cause="effect")


def _eff_draw_per_card_under(g, p, ctx, eff):
    """Draw one card for each card that was under the character in ctx
    (Donald Duck - Fred Honeywell WELL WISHES)."""
    ch = ctx.get("char")
    if ch is None:
        return
    n = len(getattr(ch, "boosted", [])) + len(getattr(ch, "under", []))
    if n:
        g.draw(p, n)
        g.emit(f"schema: draws {n} for cards under "
               f"{ch.card.base_name}")


def _eff_move_other_here(g, p, ctx, eff):
    """Move one of your other characters to the location in ctx, for free
    (Goofy - Set for Adventure FAMILY VACATION)."""
    me = ctx.get("char")
    loc = ctx.get("loc")
    if loc is None:
        return
    pool = [c for c in g.my_chars(p)
            if c.location != loc.uid
            and (me is None or c.uid != me.uid)]
    if not pool:
        return
    buddy = max(pool, key=lambda c: g.eff_lore(c))
    buddy.location = loc.uid
    g.emit(f"schema: moves {buddy.card.base_name} to {loc.card.base_name}")
    if eff.get("then"):
        apply_effect(g, p, ctx, eff["then"])


def _eff_put_top_under_source(g, p, ctx, eff):
    """Put the top card of your deck facedown under the source permanent
    (Graveyard of Christmas Future NEW ARRIVAL)."""
    src = ctx.get("source")
    pl = g.players[p]
    if src is None or not pl.deck:
        return
    store = getattr(src, "under", None)
    if store is None:
        store = getattr(src, "boosted", None)
    if store is None:
        return
    store.append(pl.deck.pop())
    g.emit(f"schema: puts a card under {src.card.base_name}")


def _eff_banish_chosen(g, p, ctx, eff):
    """Banish a chosen opposing character (Dragon Fire)."""
    from . import abilities
    tgt = abilities._best_opp_char(
        g, p, cond=lambda gg, c: _char_matches(gg, c, eff.get("filter")))
    if tgt is None:
        return
    g.emit(f"schema: banishes {tgt.card.base_name}(P{tgt.owner})")
    g.banish_char(tgt, cause="effect")


def _eff_exert_all_opposing(g, p, ctx, eff):
    """Exert every opposing character matching a filter (Ghostly Tale).
    Mass and non-targeted, so it bypasses Ward on purpose."""
    n = 0
    for c in g.my_chars(1 - p):
        if not c.exerted and _char_matches(g, c, eff.get("filter")):
            c.exerted = True
            n += 1
    g.emit(f"schema: exerts {n} opposing character(s)")


def _eff_draw_then_discard_random(g, p, ctx, eff):
    """Draw N, then discard one at random (Dangerous Plan). The engine has no
    randomness source here beyond the seeded rng, so the worst card is
    discarded -- a slightly favourable approximation, noted deliberately."""
    from . import abilities
    pl = g.players[p]
    g.draw(p, eff.get("amount", 1))
    for _ in range(eff.get("discard", 1)):
        if not pl.hand:
            break
        card = abilities._worst_hand_card(g, p)
        if card is None:
            break
        pl.hand.remove(card)
        pl.discard.append(card)
        g.emit(f"schema: discards {card.name}")


def _eff_mill_self(g, p, ctx, eff):
    """Put the top N cards of your deck into your discard
    (Preston Whitmore PRICE OF PROGRESS)."""
    pl = g.players[p]
    n = min(eff.get("amount", 1), len(pl.deck))
    for _ in range(n):
        pl.discard.append(pl.deck.pop())
    if n:
        g.emit(f"schema: mills {n} card(s)")


def _eff_ready_chosen(g, p, ctx, eff):
    """Ready one of your exerted characters, optionally locking it out of
    questing for the rest of the turn (It's Gonna Be Great!)."""
    # dispatch_quest supplies only "char", so fall back to it: without this
    # "ready ANOTHER chosen character" would happily ready the quester.
    src = ctx.get("source") or ctx.get("char")
    pool = [c for c in g.my_chars(p) if c.exerted
            and not (eff.get("exclude_self") and src is not None
                     and c.uid == getattr(src, "uid", None))]
    if not pool:
        return
    tgt = max(pool, key=lambda c: (g.eff_strength(c), g.eff_lore(c)))
    tgt.exerted = False
    g.emit(f"schema: readies {tgt.card.base_name}")
    if eff.get("no_quest"):
        g.turn_flags.add(("no_quest", tgt.uid))


def _eff_ready_self(g, p, ctx, eff):
    """Ready this character (Little John READY TO RASSLE, Maui I GOT YOUR
    BACK, Shere Khan WILD RAGE). "no_quest" adds the usual rider that the
    character cannot quest for the rest of the turn."""
    ch = ctx.get("source") or ctx.get("char")
    if ch is None:
        return
    if getattr(ch, "exerted", False):
        ch.exerted = False
        g.emit(f"schema: readies {ch.card.base_name}")
    if eff.get("no_quest"):
        g.turn_flags.add(("no_quest", ch.uid))


def _eff_put_top_under_self(g, p, ctx, eff):
    """Put the top card of your deck facedown under the character carried in
    ctx (Donald Duck - Fred Honeywell SPIRIT OF GIVING)."""
    tgt = ctx.get("char")
    pl = g.players[p]
    if tgt is None or not pl.deck:
        return
    pile = _under_pile(tgt)
    if pile is None:
        return
    pile.append(pl.deck.pop())
    g.emit(f"schema: puts a card under {tgt.card.base_name}")


def _eff_opponent_discard_per_card_under(g, p, ctx, eff):
    """Each opponent discards one card for each card under this character
    (Goofy - Ghost of Jacob Marley GRAVE OUTCOME)."""
    ch = ctx.get("char") or ctx.get("source")
    if ch is None:
        return
    n = len(getattr(ch, "boosted", [])) + len(getattr(ch, "under", []))
    if n:
        apply_effect(g, p, ctx, {"type": "opponent_discard", "amount": n})


def _eff_move_self_to_location(g, p, ctx, eff):
    """Move this character to one of your locations for free
    (Colonel Hathi HUP, TWO, THREE, FOUR)."""
    ch = ctx.get("char")
    locs = list(g.my_locs(p))
    if ch is None or not locs:
        return
    loc = max(locs, key=lambda l: g.loc_lore(l))
    ch.location = loc.uid
    g.emit(f"schema: moves {ch.card.base_name} to {loc.card.base_name}")


def _eff_buff_all_yours(g, p, ctx, eff):
    """All your characters get +N to a stat this turn (So Be It!)."""
    n = 0
    for c in g.my_chars(p):
        g.effects.append({"kind": eff.get("stat", "str"), "target": c.uid,
                          "amount": eff.get("amount", 1), "until": "eot"})
        n += 1
    g.emit(f"schema: buffs {n} of your characters")


def _eff_debuff_all_opposing(g, p, ctx, eff):
    """Every opposing character gets -N to a stat (Trust In Me). A mass,
    non-targeted effect, so it bypasses Ward on purpose."""
    until = "eot" if eff.get("duration") == "eot" else p
    for c in g.my_chars(1 - p):
        g.effects.append({"kind": eff.get("stat", "str"), "target": c.uid,
                          "amount": -abs(eff.get("amount", 1)),
                          "until": until})


def _eff_banish_target(g, p, ctx, eff):
    """Banish the character carried in ctx (the challenger, for
    Kuzco NO TOUCHY!)."""
    tgt = ctx.get("char")
    if tgt is None or tgt.uid not in g.chars:
        return
    g.emit(f"schema: banishes {tgt.card.base_name}(P{tgt.owner})")
    g.banish_char(tgt, cause="effect")


def modal_options(g, p, card_name, trigger="on_play"):
    """Legal mode indices for a card's modal ability, or None if it has none.

    Only modes that could actually do something are offered: an unusable mode
    is not a real decision and would only dilute the search.
    """
    for e in entries_for(card_name, trigger):
        opts = (e.get("effect") or {}).get("options")
        if not opts:
            continue
        live = [i for i, o in enumerate(opts)
                if check_condition(g, p, {}, o.get("condition"))
                and _option_actionable(g, p, o)]
        return live or [0]
    return None


def _option_actionable(g, p, o):
    """Could this option affect anything right now?"""
    if o.get("type") not in ("banish_chosen", "exert_chosen", "deal_damage"):
        return True
    from . import abilities
    return abilities._best_opp_char(
        g, p, cond=lambda gg, c: _char_matches(gg, c, o.get("filter")),
        notify=False) is not None


def _eff_choose_one(g, p, ctx, eff):
    """Modal "choose one" (Baloo ROLL WITH IT, Tod - Playful Kit).

    Options are resolved by a fixed heuristic rather than by search: take the
    first option whose effect can actually do something, preferring the one
    that does not hand the opponent value. Symmetric options (each player
    draws / each player discards) are decided on hand sizes.
    """
    opts = eff.get("options") or []
    if not opts:
        return
    # An option whose own condition fails is not a legal choice
    # (Firefly Swarm's second mode needs 2+ cards discarded this turn).
    opts = [o for o in opts
            if check_condition(g, p, ctx, o.get("condition"))]
    if not opts:
        return
    # Drop options that could not do anything: a targeted mode whose filter
    # matches no opposing character is a wasted choice, and picking it would
    # make the card look weaker than it is.
    def _actionable(o):
        if o.get("type") not in ("banish_chosen", "exert_chosen",
                                 "deal_damage"):
            return True
        from . import abilities
        return abilities._best_opp_char(
            g, p, cond=lambda gg, c: _char_matches(gg, c, o.get("filter")),
            notify=False) is not None
    live = [o for o in opts if _actionable(o)]
    if live:
        opts = live
    # If the player declared a mode when choosing the action, honour it --
    # that is the whole point of putting the choice in the action space.
    mode = (ctx.get("params") or {}).get("mode")
    if mode is not None:
        allopts = eff.get("options") or []
        if 0 <= mode < len(allopts):
            chosen = allopts[mode]
            if check_condition(g, p, ctx, chosen.get("condition")):
                g.emit(f"schema: chooses mode {mode} ({chosen.get('type')})")
                apply_effect(g, p, ctx, chosen)
                return
    pick = None
    for o in opts:
        t = o.get("type")
        if t == "each_player_draw" and len(g.players[p].hand) \
                <= len(g.players[1 - p].hand):
            pick = o
            break
        if t == "opponent_discard" and len(g.players[1 - p].hand) > 0 \
                and len(g.players[1 - p].hand) >= len(g.players[p].hand):
            pick = o
            break
    if pick is None:
        pick = opts[0]
    g.emit(f"schema: chooses {pick.get('type')}")
    apply_effect(g, p, ctx, pick)


def _eff_damage_conditional(g, p, ctx, eff):
    """Deal N damage, or M instead when a condition holds
    (Helga Sinclair - Prepared for Anything)."""
    amount = eff.get("amount", 1)
    if check_condition(g, p, ctx, eff.get("upgrade_if")):
        amount = eff.get("upgraded_amount", amount)
    apply_effect(g, p, ctx, {"type": "deal_damage", "amount": amount,
                             "target": eff.get("target", "chosen_opposing"),
                             "filter": eff.get("filter")})


def _eff_banish_same_name(g, p, ctx, eff):
    """Banish a chosen item or location and every other one sharing its name
    (Sabotage). Targets the opponent's board, most expensive first."""
    cands = [(i, "item") for i in g.items[1 - p]] \
        + [(l, "loc") for l in g.my_locs(1 - p)]
    if not cands:
        return
    obj, _kind = max(cands, key=lambda t: t[0].card.cost)
    name = obj.card.name
    for it in list(g.items[0]) + list(g.items[1]):
        if it.card.name == name:
            g.banish_item(it)
    for lo in list(g.locs.values()):
        if lo.card.name == name:
            g.banish_loc(lo)
    g.emit(f"schema: banishes every {name}")


def _eff_put_top_under_boosted(g, p, ctx, eff):
    """Put the top card of your deck facedown under one of your permanents
    with Boost (Emily Quackfaster RECOMMENDED READING)."""
    from . import abilities
    pl = g.players[p]
    if not pl.deck:
        return
    targets = [c for c in g.my_chars(p) if abilities.boost_cost(c.card)]
    targets += [l for l in g.my_locs(p) if abilities.boost_cost(l.card)]
    if not targets:
        return
    tgt = max(targets, key=lambda o: o.card.cost)
    pile = _under_pile(tgt)
    if pile is None:
        return
    pile.append(pl.deck.pop())
    g.emit(f"schema: puts a card under {tgt.card.base_name}")


def _eff_damage_counter_each_opposing(g, p, ctx, eff):
    """Put N damage counters on every opposing character (Bellwether
    VENDETTA). Counters are not damage dealt, so Resist does not apply."""
    n = eff.get("amount", 1)
    for c in list(g.my_chars(1 - p)):
        if c.uid in g.chars:
            g.deal_damage(c, n, apply_resist=False)
            if g.winner is not None:
                return


def _eff_move_two_to_location(g, p, ctx, eff):
    """Move this character and one other to the same location, for free
    (Russell - Junior Wilderness Explorer)."""
    me = ctx.get("char")
    locs = list(g.my_locs(p))
    if me is None or not locs:
        return
    loc = max(locs, key=lambda l: g.loc_lore(l))
    others = [c for c in g.my_chars(p)
              if c.uid != me.uid and c.location != loc.uid]
    me.location = loc.uid
    moved = [me.card.base_name]
    if others:
        buddy = max(others, key=lambda c: g.eff_lore(c))
        buddy.location = loc.uid
        moved.append(buddy.card.base_name)
        buff = eff.get("buff")
        if buff:
            g.effects.append({"kind": buff.get("stat", "str"),
                              "target": buddy.uid,
                              "amount": buff.get("amount", 1),
                              "until": "eot"})
    g.emit(f"schema: moves {', '.join(moved)} to {loc.card.base_name}")


def _eff_damage_each(g, p, ctx, eff):
    """Deal N damage to every opposing character matching a filter. A mass,
    non-targeted effect, so it bypasses _best_opp_char and Ward on purpose
    (To Wither A Flower)."""
    n = eff.get("amount", 1)
    for c in list(g.my_chars(1 - p)):
        if c.uid in g.chars and _char_matches(g, c, eff.get("filter")):
            g.deal_damage(c, n)
            if g.winner is not None:
                return


def _eff_gain_lore_equal_strength(g, p, ctx, eff):
    """Gain lore equal to this character's Strength, capped
    (Mulan - Resourceful Recruit)."""
    ch = ctx.get("char")
    if ch is None:
        return
    amount = min(g.eff_strength(ch), eff.get("max", 99))
    if amount > 0:
        g.gain_lore(p, amount)


def _eff_exert_chosen(g, p, ctx, eff):
    """Exert a chosen opposing character (Boomer - Has the Beak)."""
    from . import abilities
    tgt = abilities._best_opp_char(
        g, p, cond=lambda gg, c: not c.exerted
        and _char_matches(gg, c, eff.get("filter")))
    if tgt is None:
        return
    tgt.exerted = True
    g.emit(f"schema: exerts {tgt.card.base_name}(P{tgt.owner})")


def _eff_banish_all_locations(g, p, ctx, eff):
    for loc in list(g.locs.values()):
        g.banish_loc(loc)


def _eff_reveal_hand(g, p, ctx, eff):
    """Chosen player reveals their hand. The engine has perfect information
    internally, so this is informational only and has no mechanical effect."""
    g.emit(f"schema: P{1 - p} reveals their hand "
           f"({len(g.players[1 - p].hand)} cards)")


def _eff_grant_keyword_opposing(g, p, ctx, eff):
    """Grant a keyword to a chosen opposing character
    (Stitch - Naughty Experiment)."""
    from . import abilities
    tgt = abilities._best_opp_char(g, p)
    if tgt is None:
        return
    until = "eot" if eff.get("duration") == "eot" else p
    g.effects.append({"kind": eff.get("keyword", "reckless"),
                      "target": tgt.uid, "amount": 0, "until": until})
    g.emit(f"schema: {tgt.card.base_name} gains {eff.get('keyword')}")


def _eff_buff_your_keyword_chars(g, p, ctx, eff):
    """Your characters with KEYWORD get +N to a stat this turn
    (Copper - Champion of the Forest)."""
    from . import abilities
    fn = {"evasive": abilities.has_evasive, "ward": abilities.has_ward,
          "reckless": abilities.has_reckless}.get(eff.get("keyword", "evasive"))
    if fn is None:
        return
    n = 0
    for c in g.my_chars(p):
        if fn(g, c):
            g.effects.append({"kind": eff.get("stat", "lore"),
                              "target": c.uid,
                              "amount": eff.get("amount", 1), "until": "eot"})
            n += 1
    g.emit(f"schema: buffs {n} character(s)")


def _eff_banish_item(g, p, ctx, eff):
    """Banish chosen opposing item (Benja WE HAVE A CHOICE)."""
    items = list(g.items[1 - p])
    if not items:
        return
    tgt = max(items, key=lambda i: i.card.cost)
    g.emit(f"schema: banishes item {tgt.card.base_name}(P{1 - p})")
    g.banish_item(tgt)


def _eff_deal_damage_multi(g, p, ctx, eff):
    """Deal N damage to up to COUNT different chosen characters
    (Robin Hood EXPERT SHOT). "Up to" tolerates fewer targets."""
    from . import abilities
    n = eff.get("amount", 1)
    picked = []
    for _ in range(eff.get("count", 1)):
        tgt = abilities._best_opp_char(
            g, p, cond=lambda gg, c: c.uid not in [x.uid for x in picked])
        if tgt is None:
            break
        picked.append(tgt)
    for t in picked:
        g.deal_damage(t, n)
        if g.winner is not None:
            return


def _eff_grant_resist(g, p, ctx, eff):
    """Grant Resist +N to one of your characters (Discarded Armor)."""
    pool = list(g.my_chars(p))
    if not pool:
        return
    tgt = max(pool, key=lambda c: (g.eff_lore(c), g.eff_strength(c)))
    until = "eot" if eff.get("duration") == "eot" else p
    g.effects.append({"kind": "resist", "target": tgt.uid,
                      "amount": eff.get("amount", 1), "until": until})
    g.emit(f"schema: {tgt.card.base_name} gains Resist "
           f"+{eff.get('amount', 1)}")


def _eff_enter_exerted_for(g, p, ctx, eff):
    """Optionally enter play exerted for an effect (Lord MacGuffin
    WAIT FOR IT...). The entry is condition-gated, so by the time this runs
    the payoff is known to exist."""
    ch = ctx.get("char")
    if ch is None:
        return
    ch.exerted = True
    g.emit(f"schema: {ch.card.base_name} enters play exerted")
    inner = eff.get("then")
    if inner:
        apply_effect(g, p, ctx, inner)


def _eff_conditional_discard(g, p, ctx, eff):
    """Discard a card only when the condition holds (Launchpad: discard
    *unless* you control Darkwing Duck)."""
    from . import abilities
    if not check_condition(g, p, ctx, eff.get("condition")):
        return
    pl = g.players[p]
    card = abilities._worst_hand_card(g, p)
    if card is None:
        return
    pl.hand.remove(card)
    pl.discard.append(card)
    g.emit(f"schema: discards {card.name}")


def _eff_discard_to_damage(g, p, ctx, eff):
    """Discard a card as a cost to deal damage (David Xanatos). Skipped when
    the hand is empty, so the damage is never free."""
    from . import abilities
    pl = g.players[p]
    if not pl.hand:
        return
    card = abilities._worst_hand_card(g, p)
    if card is None:
        return
    pl.hand.remove(card)
    pl.discard.append(card)
    g.emit(f"schema: discards {card.name} to deal damage")
    apply_effect(g, p, ctx, {"type": "deal_damage",
                             "amount": eff.get("amount", 1),
                             "target": "chosen_opposing"})


def _eff_cant_challenge(g, p, ctx, eff):
    """Up to N chosen opposing characters can't challenge during their next
    turn. Recorded as a timed effect keyed to the *victim's* next turn, so it
    survives our turn and lapses at the start of theirs."""
    from . import abilities
    picked = []
    for _ in range(eff.get("count", 1)):
        tgt = abilities._best_opp_char(
            g, p, cond=lambda gg, c: c.uid not in [x.uid for x in picked])
        if tgt is None:
            break
        picked.append(tgt)
        g.effects.append({"kind": "no_challenge", "target": tgt.uid,
                          "amount": 0, "until": 1 - p})
    if picked:
        g.emit("schema: " + ", ".join(c.card.base_name for c in picked)
               + " can't challenge next turn")


def _eff_reveal_top_play_or_discard(g, p, ctx, eff):
    """Reveal the top card; play it as if in hand if affordable, otherwise
    put it in the discard (Kristoff's Lute)."""
    pl = g.players[p]
    if not pl.deck:
        return
    card = pl.deck[-1]
    filt = eff.get("filter")
    if (filt is None or _card_matches(card, filt)) \
            and g.play_cost(p, card) <= pl.ink_ready:
        pl.deck.pop()
        g.emit(f"schema: reveals {card.name} and plays it")
        g._play_card(p, card, {})
        return
    pl.deck.pop()
    pl.discard.append(card)
    g.emit(f"schema: reveals {card.name} and discards it")


def _eff_play_from_discard_then_bottom(g, p, ctx, eff):
    """Play a matching card from your discard for free, then put it on the
    bottom of your deck instead of back in the discard
    (Lady Tremaine EXPEDIENT SCHEMES)."""
    pl = g.players[p]
    pool = [c for c in pl.discard if _card_matches(c, eff.get("filter"))]
    if not pool:
        return
    pick = max(pool, key=lambda c: c.cost)
    pl.discard.remove(pick)
    g.emit(f"schema: plays {pick.name} from discard (free)")
    g._play_card(p, pick, {}, free=True)
    # actions resolve into the discard; move it to the bottom instead
    if pick in pl.discard:
        pl.discard.remove(pick)
        pl.deck.insert(0, pick)


def _eff_play_same_name_free(g, p, ctx, eff):
    """Play a character from hand sharing a name with the one just banished
    (Vine Pod REGENERATE). ctx['banished_name'] is set by the cost."""
    want = ctx.get("banished_name")
    if not want:
        return
    pl = g.players[p]
    pool = [c for c in pl.hand if c.is_character and c.base_name == want]
    if not pool:
        return
    pick = max(pool, key=lambda c: c.cost)
    g.emit(f"schema: plays {pick.name} for free")
    g._play_card(p, pick, {}, free=True)


def _eff_reveal_hand_discard_type(g, p, ctx, eff):
    """Chosen opponent reveals their hand and discards a card of a type you
    pick (Goldie O'Gilt CLAIM JUMPER). We choose, so take the best one."""
    opp = 1 - p
    pl = g.players[opp]
    pool = [c for c in pl.hand if _card_matches(c, eff.get("filter"))]
    if not pool:
        return
    pick = max(pool, key=lambda c: c.cost)
    pl.hand.remove(pick)
    pl.discard.append(pick)
    g.emit(f"schema: P{opp} reveals and discards {pick.name}")


def _eff_discard_to_bottom_for_lore(g, p, ctx, eff):
    """Put a matching card from a player's discard on the bottom of their
    deck to gain lore (Goldie O'Gilt STRIKE GOLD). Targets the opponent's
    discard, since removing their recursion is worth more than ours."""
    for who in (1 - p, p):
        pl = g.players[who]
        pool = [c for c in pl.discard if _card_matches(c, eff.get("filter"))]
        if not pool:
            continue
        pick = max(pool, key=lambda c: c.cost)
        pl.discard.remove(pick)
        pl.deck.insert(0, pick)
        g.emit(f"schema: bottoms {pick.name} from P{who}'s discard")
        if eff.get("then"):
            apply_effect(g, p, ctx, eff["then"])
        return


def _eff_dig_reveal_to_hand(g, p, ctx, eff):
    """Look at the top N, reveal one matching card to hand, bottom the rest
    (Amazu's Inkcaster). A filtered variant of look_at_top."""
    apply_effect(g, p, ctx, {"type": "look_at_top",
                             "count": eff.get("count", 4),
                             "destination": "hand",
                             "filter": eff.get("filter")})


def _eff_banish_own_to_draw(g, p, ctx, eff):
    """Banish one of your characters to draw; draw more if it had a card
    under it (Time to Go!). Spends the least valuable body."""
    pool = list(g.my_chars(p))
    if not pool:
        return
    victim = min(pool, key=lambda c: (g.eff_lore(c), g.eff_strength(c),
                                      c.card.cost))
    boosted = bool(getattr(victim, "boosted", None)
                   or getattr(victim, "under", None))
    n = eff.get("bonus_amount", eff.get("amount", 1)) if boosted \
        else eff.get("amount", 1)
    g.emit(f"schema: banishes {victim.card.base_name} to draw {n}")
    g.banish_char(victim, cause="effect")
    g.draw(p, n)


def _eff_banish_own_then(g, p, ctx, eff):
    """Banish another of your characters; if you do, run a follow-on effect
    (Sid Phillips PLAYTIME'S OVER)."""
    me = ctx.get("char")
    pool = [c for c in g.my_chars(p)
            if me is None or c.uid != me.uid]
    if not pool:
        return
    victim = min(pool, key=lambda c: (g.eff_lore(c), g.eff_strength(c),
                                      c.card.cost))
    g.emit(f"schema: banishes own {victim.card.base_name}")
    g.banish_char(victim, cause="effect")
    if eff.get("then") and g.winner is None:
        apply_effect(g, p, ctx, eff["then"])


def _eff_banish_up_to_total_strength(g, p, ctx, eff):
    """Banish any number of chosen opposing characters whose total Strength
    is at most N (The Leviathan). Greedy: take the strongest that still
    fits, which maximises what is removed."""
    from . import abilities
    budget = eff.get("total", 0)
    taken = []
    while True:
        cand = [c for c in g.my_chars(1 - p)
                if not abilities.has_ward(g, c)
                and c.uid not in [t.uid for t in taken]
                and g.eff_strength(c) <= budget]
        if not cand:
            break
        pick = max(cand, key=lambda c: g.eff_strength(c))
        budget -= g.eff_strength(pick)
        taken.append(pick)
    for c in taken:
        g.emit(f"schema: banishes {c.card.base_name}(P{c.owner})")
        g.banish_char(c, cause="effect")
        if g.winner is not None:
            return


def _eff_opponent_lose_lore_per_damage(g, p, ctx, eff):
    """Each opponent loses lore equal to the damage on one of your damaged
    characters, capped (Nani's Payback)."""
    pool = [c for c in g.my_chars(p) if c.damage > 0]
    if not pool:
        return
    src = max(pool, key=lambda c: c.damage)
    n = min(src.damage, eff.get("max", 99))
    if n:
        apply_effect(g, p, ctx, {"type": "opponent_lose_lore", "amount": n})


def _eff_draw_per_damage_then_banish(g, p, ctx, eff):
    """Draw cards equal to the damage on one of your characters, then banish
    it (Dinner Bell). Chooses the most damaged body, since it is the one
    closest to being lost anyway."""
    pool = [c for c in g.my_chars(p) if c.damage > 0]
    if not pool:
        return
    src = max(pool, key=lambda c: c.damage)
    n = src.damage
    g.draw(p, n)
    g.emit(f"schema: draws {n} then banishes {src.card.base_name}")
    g.banish_char(src, cause="effect")


def _eff_drain_then_draw_per_lore(g, p, ctx, eff):
    """Each opponent loses N lore; draw one card per lore actually lost
    (Scrooge McDuck). An opponent already at 0 loses nothing, so no card."""
    want = eff.get("amount", 1)
    before = g.players[1 - p].lore
    apply_effect(g, p, ctx, {"type": "opponent_lose_lore", "amount": want})
    lost = before - g.players[1 - p].lore
    if lost > 0:
        g.draw(p, lost)
        g.emit(f"schema: draws {lost} for lore lost")


def _eff_each_player_gain_lore(g, p, ctx, eff):
    """Each player gains N lore, the active player first (I2I)."""
    n = eff.get("amount", 1)
    for who in (p, 1 - p):
        g.gain_lore(who, n)
        if g.winner is not None:
            return


def _eff_each_player_draw(g, p, ctx, eff):
    """Each player draws N, active player first (Miriam Mendelsohn)."""
    n = eff.get("amount", 1)
    for who in (p, 1 - p):
        g.draw(who, n)
    g.emit(f"schema: each player draws {n}")


def _eff_self_to_deck_top(g, p, ctx, eff):
    """Put this character from play onto the top of its owner's deck
    (Kevin - Flightless Bird). Cards underneath it go to the discard, the
    same as any other way of leaving play."""
    ch = ctx.get("char")
    if ch is None or ch.uid not in g.chars:
        return
    owner = ch.owner
    pl = g.players[owner]
    pl.discard.extend(ch.under)
    pl.discard.extend(ch.boosted)
    g.chars.pop(ch.uid, None)
    pl.deck.append(ch.card)              # top of deck is the end of the list
    g.emit(f"schema: {ch.card.base_name}(P{owner}) returns to the top of the deck")


def _eff_move_damage(g, p, ctx, eff):
    """Move up to N damage from one of your characters onto this one, then
    optionally dump the accumulated damage onto an opposing character
    (Luisa Madrigal I CAN TAKE IT).

    Heuristic: pull from your most-damaged other character, since that is the
    one nearest to being banished.
    """
    me = ctx.get("char")
    if me is None:
        return
    n = eff.get("amount", 1)
    donors = [c for c in g.my_chars(p) if c.uid != me.uid and c.damage > 0]
    if donors:
        src = max(donors, key=lambda c: c.damage)
        moved = min(n, src.damage)
        src.damage -= moved
        me.damage += moved
        g.emit(f"schema: moves {moved} damage {src.card.base_name} -> "
               f"{me.card.base_name}")
    # Second clause: if this character is at the threshold, dump it all.
    thresh = eff.get("dump_at")
    if thresh is not None and me.damage >= thresh:
        tgt = abilities._best_opp_char(g, p)
        if tgt is not None:
            amount = me.damage
            me.damage = 0
            g.emit(f"schema: dumps {amount} damage onto "
                   f"{tgt.card.base_name}(P{tgt.owner})")
            g.deal_damage(tgt, amount, apply_resist=False)
        # Moving damage off a character never banishes it, so no check here.


def _eff_reveal_and_play(g, p, ctx, eff):
    """Reveal the top card of your deck; play it if you can pay, otherwise
    discard it (Dash Parr FOLLOW ME!).

    The reveal is a "may", and revealing with no ink available can only mill,
    so it is declined in that case. Otherwise reveal and play when affordable.
    """
    pl = g.players[p]
    if not pl.deck:
        return
    if pl.ink_ready <= 0:
        return                      # would be pure self-mill
    card = pl.deck[-1]
    if card.is_action or card.is_character or card.is_item or card.is_location:
        cost = g.play_cost(p, card)
        if cost <= pl.ink_ready:
            pl.deck.pop()
            g.emit(f"schema: reveals {card.name} and plays it")
            g._play_card(p, card, {})
            return
    pl.deck.pop()
    pl.discard.append(card)
    g.emit(f"schema: reveals {card.name} and discards it")


_EFFECTS = {
    "conditional_discard": _eff_conditional_discard,
    "discard_to_damage": _eff_discard_to_damage,
    "cant_challenge": _eff_cant_challenge,
    "reveal_top_play_or_discard": _eff_reveal_top_play_or_discard,
    "play_from_discard_then_bottom": _eff_play_from_discard_then_bottom,
    "play_same_name_free": _eff_play_same_name_free,
    "reveal_hand_discard_type": _eff_reveal_hand_discard_type,
    "discard_to_bottom_for_lore": _eff_discard_to_bottom_for_lore,
    "dig_reveal_to_hand": _eff_dig_reveal_to_hand,
    "banish_own_to_draw": _eff_banish_own_to_draw,
    "banish_own_then": _eff_banish_own_then,
    "banish_up_to_total_strength": _eff_banish_up_to_total_strength,
    "opponent_lose_lore_per_damage": _eff_opponent_lose_lore_per_damage,
    "draw_per_damage_then_banish": _eff_draw_per_damage_then_banish,
    "drain_then_draw_per_lore": _eff_drain_then_draw_per_lore,
    "each_player_gain_lore": _eff_each_player_gain_lore,
    "sequence": _eff_sequence,
    "put_top_into_inkwell": _eff_put_top_into_inkwell,
    "exert_all_inkwell": _eff_exert_all_inkwell,
    "discard_to_bottom": _eff_discard_to_bottom,
    "then_if_moved": _eff_then_if_moved,
    "discard_hand_then_return": _eff_discard_hand_then_return,
    "opponent_banish_own": _eff_opponent_banish_own,
    "draw_per_card_under": _eff_draw_per_card_under,
    "move_other_here": _eff_move_other_here,
    "put_top_under_source": _eff_put_top_under_source,
    "banish_chosen": _eff_banish_chosen,
    "exert_all_opposing": _eff_exert_all_opposing,
    "draw_then_discard_random": _eff_draw_then_discard_random,
    "mill_self": _eff_mill_self,
    "ready_chosen": _eff_ready_chosen,
    "ready_self": _eff_ready_self,
    "put_top_under_self": _eff_put_top_under_self,
    "opponent_discard_per_card_under": _eff_opponent_discard_per_card_under,
    "move_self_to_location": _eff_move_self_to_location,
    "buff_all_yours": _eff_buff_all_yours,
    "debuff_all_opposing": _eff_debuff_all_opposing,
    "banish_target": _eff_banish_target,
    "choose_one": _eff_choose_one,
    "damage_conditional": _eff_damage_conditional,
    "banish_same_name": _eff_banish_same_name,
    "put_top_under_boosted": _eff_put_top_under_boosted,
    "damage_counter_each_opposing": _eff_damage_counter_each_opposing,
    "move_two_to_location": _eff_move_two_to_location,
    "damage_each": _eff_damage_each,
    "gain_lore_equal_strength": _eff_gain_lore_equal_strength,
    "exert_chosen": _eff_exert_chosen,
    "banish_all_locations": _eff_banish_all_locations,
    "reveal_hand": _eff_reveal_hand,
    "grant_keyword_opposing": _eff_grant_keyword_opposing,
    "buff_your_keyword_chars": _eff_buff_your_keyword_chars,
    "banish_item": _eff_banish_item,
    "deal_damage_multi": _eff_deal_damage_multi,
    "grant_resist": _eff_grant_resist,
    "enter_exerted_for": _eff_enter_exerted_for,
    "draw_then_discard": _eff_draw_then_discard,
    "return_from_discard": _eff_return_from_discard,
    "return_cards_under": _eff_return_cards_under,
    "each_player_draw": _eff_each_player_draw,
    "self_to_deck_top": _eff_self_to_deck_top,
    "move_damage": _eff_move_damage,
    "reveal_and_play": _eff_reveal_and_play,
    "mass_grant_keyword": _eff_mass_grant_keyword,
    "quest_lock": _eff_quest_lock,
    "banish_location": _eff_banish_location,
    "opponent_scatter": _eff_opponent_scatter,
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
    if eff.get("type") in ("play_free_if", "play_cost_reduction",
                           "shift_alias", "static_no_ready", "enters_exerted",
                           "static_self_stat",
                           "static_self_lore", "static_self_keyword",
                           "static_location_resist", "static_location_lore",
                           "shift_onto_names", "team_keyword", "team_stat",
                           "location_aura_stat", "enters_with_damage",
                           "static_location_keyword", "free_move_here",
                           "no_quest_or_challenge_unless",
                           "team_strength_floor",
                           "classification_cant_quest",
                           "opposing_items_cant_ready",
                           "no_challenge_damage", "move_cost_reduction",
                           "play_free_via_bottom", "opponent_cant_play"):
        return          # consumed by the static hooks, not dispatched
    fn = _EFFECTS.get(eff.get("type"))
    if fn is None:
        g.emit(f"schema: unknown effect {eff.get('type')} (skipped)")
        return
    fn(g, p, ctx, eff)


# ---------------------------------------------------------------------
# Dispatchers, called from abilities.py trigger hooks
# ---------------------------------------------------------------------
def _once_key(e, ctx):
    src = ctx.get("source") or ctx.get("char")
    return ("once", e.get("once_id") or id(e),
            getattr(src, "uid", None))


def _run(g, p, ctx, ents):
    for e in ents:
        if "effect" not in e:
            continue  # e.g. {"impl": "python"} marker entries
        # "Once during your turn, ..." -- one use per source per turn.
        limit = e.get("uses_per_turn") or (1 if e.get("once_per_turn") else 0)
        if limit:
            key = _once_key(e, ctx)
            used = getattr(g, "use_counts", None)
            if used is None:
                used = g.use_counts = {}
            if used.get(key, 0) >= limit:
                continue
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
            src = ctx.get("source")
            if cost.get("banish_self") and src is None:
                continue
            if cost.get("ink", 0):
                g.pay_ink(p, cost["ink"])
            for _ in range(cost.get("discard", 0)):
                card = abilities._worst_hand_card(g, p)
                if card is None:
                    break
                pl.hand.remove(card)
                pl.discard.append(card)
                g.emit(f"schema: discards {card.name} (cost)")
            if cost.get("banish_self"):
                if _obj_is_char(src):
                    g.banish_char(src, cause="effect")
                elif src in g.items[p]:
                    g.banish_item(src)
        if limit:
            key = _once_key(e, ctx)
            g.use_counts[key] = g.use_counts.get(key, 0) + 1
        apply_effect(g, p, ctx, e["effect"])
        if g.winner is not None:
            return


def dispatch_play(g, p, card, obj, params):
    ents = entries_for(card.name, "on_play")
    if ents:
        _run(g, p, {"card": card, "params": params,
                    "char": obj if card.is_character else None}, ents)
    if params and params.get("shift"):
        dispatch_shift(g, p, card, obj, params)
    if card.is_character:
        dispatch_play_character(g, p, card, obj)


def dispatch_play_character(g, p, card, obj):
    """'Whenever you play a character' watchers sitting on your own permanents
    (The Robot Queen). ctx["source"] is the watcher, so a banish_self cost
    knows what to banish; ctx["char"] is the character just played."""
    for src in list(g.items[p]) + list(g.my_chars(p)) + list(g.my_locs(p)):
        ents = entries_for(src.card.name, "on_play_character")
        if not ents:
            continue
        is_self = _obj_is_char(src) and obj is not None \
            and src.uid == obj.uid
        for e in ents:
            # "another character" watchers skip their own arrival; "this or
            # another" watchers (Violet Parr HEROIC SYNERGY) do not.
            if is_self and not e.get("include_self"):
                continue
            want = e.get("played_classification")
            if want and not (card.classifications & set(want)):
                continue
            minstr = e.get("played_min_strength")
            if minstr is not None:
                if obj is None or g.eff_strength(obj) < minstr:
                    continue
            _run(g, p, {"card": src.card, "char": obj, "source": src}, [e])
            if g.winner is not None:
                return


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
#
# "exert" is opt-in. Some abilities cost only ink ("1 Ink -- ..." on Luisa
# Madrigal and Ling), and those neither require a ready body nor exert one,
# so they can be used more than once a turn while the ink lasts.
# ---------------------------------------------------------------------
def activated_entries(card_name):
    return entries_for(card_name, "activated")


def _obj_is_char(obj):
    """True only for CharInPlay. LocInPlay also carries .damage, so testing
    for that misidentifies locations as characters; .boosted is unique to
    characters."""
    return hasattr(obj, "boosted")


def _under_pile(obj):
    """The list of facedown cards under a permanent. Characters keep Boost
    cards in .boosted; items and locations use .under."""
    pile = getattr(obj, "boosted", None)
    if pile is None:
        pile = getattr(obj, "under", None)
    return pile


def _cards_under(obj):
    return len(getattr(obj, "boosted", None) or []) \
        + len(getattr(obj, "under", None) or [])


def can_activate(g, p, obj, entry):
    """Is this activated ability legally available right now?

    Also checks the entry condition, so an ability that would resolve to
    nothing is never offered. Without this, Enigmatic Inkcaster would expose
    "exert -> gain 1 lore" before you had played 2 cards and the policy could
    burn the exert for no effect.
    """
    cost = entry.get("cost") or {}
    pl = g.players[p]
    ctx = {"card": obj.card, "char": obj if _obj_is_char(obj) else None}
    if not check_condition(g, p, ctx, entry.get("condition")):
        return False
    if cost.get("exert", False):
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
    if cost.get("self_damage"):
        # paying it must not be lethal, or the ability kills its own source
        if not _obj_is_char(obj):
            return False
        if g.eff_willpower(obj) - obj.damage <= cost["self_damage"]:
            return False
    return True


def _pay_activation_cost(g, p, obj, entry, ctx):
    """Pay the cost. Returns False if it could not be paid."""
    cost = entry.get("cost") or {}
    pl = g.players[p]
    if not can_activate(g, p, obj, entry):
        return False
    if cost.get("exert", False):
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
            ctx["banished_name"] = victim.card.base_name
            g.emit(f"schema: banishes own {victim.card.base_name} (cost)")
            g.banish_char(victim, cause="effect")
    if cost.get("self_damage"):
        obj.damage += cost["self_damage"]
        g.emit(f"schema: {obj.card.base_name} takes {cost['self_damage']} "
               f"damage (cost)")
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


def dispatch_shift(g, p, card, obj, params):
    """'When you shift this character' triggers."""
    ents = entries_for(card.name, "on_shift")
    if ents:
        _run(g, p, {"card": card, "char": obj, "params": params}, ents)


def dispatch_action_damage(g, p, victim):
    """'Whenever one of your actions deals damage to an opposing character'
    watchers on your own characters (Merida - Formidable Archer STEADY AIM)."""
    for src in list(g.my_chars(p)):
        ents = entries_for(src.card.name, "on_action_damage")
        if ents:
            _run(g, p, {"card": src.card, "char": victim, "source": src}, ents)
        if g.winner is not None:
            return


def dispatch_play_type(g, p, card):
    """'Whenever you play a location / an action' watchers on your own
    permanents (Ellie Fredricksen, Aladdin - On the Edge of Adventure)."""
    trig = ("on_play_location" if card.is_location
            else "on_play_action" if card.is_action else None)
    if trig is None:
        return
    for src in list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p)):
        ents = entries_for(src.card.name, trig)
        if not ents:
            continue
        _run(g, p, {"card": src.card,
                    "char": src if _obj_is_char(src) else None,
                    "source": src}, ents)
        if g.winner is not None:
            return


def dispatch_challenged(g, defender, attacker):
    """'Whenever this character is challenged' (The Witch) and 'whenever one
    of your <classification> characters is challenged' (Peter Pan - Created
    by the Vine) watchers, on the defending side."""
    p = defender.owner
    ents = entries_for(defender.card.name, "on_challenged")
    if ents:
        _run(g, p, {"card": defender.card, "char": attacker,
                    "source": defender}, ents)
    for src in list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p)):
        ents = entries_for(src.card.name, "on_ally_challenged")
        for e in ents:
            want = e.get("defender_classification")
            if want and not (defender.card.classifications & set(want)):
                continue
            if e.get("defender_has_card_under") and not (
                    getattr(defender, "boosted", None)
                    or getattr(defender, "under", None)):
                continue
            _run(g, p, {"card": src.card, "char": attacker,
                        "source": src}, [e])
            if g.winner is not None:
                return


def dispatch_leave_play(g, ch):
    """'When this character leaves play' -- banished, bounced or decked."""
    ents = entries_for(ch.card.name, "on_leave_play")
    if ents:
        _run(g, ch.owner, {"card": ch.card, "char": ch, "source": ch}, ents)


def static_location_lore(g, loc):
    """Conditional Lore on a location (Paradise Falls QUITE A SIGHT)."""
    total = 0
    for e in entries_for(loc.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") != "static_location_lore":
            continue
        if not check_condition(g, loc.owner, {"card": loc.card, "loc": loc},
                               e.get("condition")):
            continue
        if eff.get("per") == "character_here":
            total += eff.get("amount", 0) * sum(
                1 for c in g.chars.values() if c.location == loc.uid)
        else:
            total += eff.get("amount", 0)
    return total


def dispatch_turn_end(g, p):
    """'At the end of your turn' triggers on your permanents."""
    for src in list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p)):
        ents = entries_for(src.card.name, "on_turn_end")
        if ents:
            _run(g, p, {"card": src.card,
                        "char": src if _obj_is_char(src) else None,
                        "source": src}, ents)
            if g.winner is not None:
                return


def dispatch_turn_start(g, p):
    """'At the start of your turn' triggers on your permanents."""
    for src in list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p)):
        ents = entries_for(src.card.name, "on_turn_start")
        if ents:
            _run(g, p, {"card": src.card,
                        "char": src if _obj_is_char(src) else None,
                        "source": src}, ents)


def dispatch_own_song(g, p):
    """'Whenever you play a song' watchers on your own permanents
    (P.J. Pete - Caught Up in the Music). The mirror of
    dispatch_opponent_song."""
    for src in list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p)):
        ents = entries_for(src.card.name, "on_song_played")
        if ents:
            _run(g, p, {"card": src.card,
                        "char": src if _obj_is_char(src) else None,
                        "source": src}, ents)
            if g.winner is not None:
                return


def dispatch_opponent_song(g, p):
    """'Whenever an opponent plays a song' watchers on p's permanents."""
    for src in list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p)):
        ents = entries_for(src.card.name, "on_opponent_song")
        if ents:
            _run(g, p, {"card": src.card,
                        "char": src if _obj_is_char(src) else None,
                        "source": src}, ents)


def dispatch_move(g, ch, loc):
    """Movement watchers, fired from abilities.on_move.

    "on_move_self" sits on the moving character (Goofy - Set for Adventure);
    "on_move_here" sits on the destination location (Graveyard of Christmas
    Future, The Bitterwood).
    """
    p = ch.owner
    ents = entries_for(ch.card.name, "on_move_self")
    if ents:
        _run(g, p, {"card": ch.card, "char": ch, "source": ch, "loc": loc},
             ents)
    ents = entries_for(loc.card.name, "on_move_here")
    for e in ents:
        minstr = e.get("moved_min_strength")
        if minstr is not None and g.eff_strength(ch) < minstr:
            continue
        _run(g, p, {"card": loc.card, "char": ch, "source": loc, "loc": loc},
             [e])
        if g.winner is not None:
            return


def dispatch_challenge_at_location(g, attacker, defender):
    """Location watchers for challenges happening at that location:
    "whenever a character here challenges" (Beast's Castle - Winter Gardens)
    and "whenever a character is challenged while here" (Pizza Planet)."""
    for loc_uid, trig, who in ((getattr(attacker, "location", None),
                                "on_challenge_from_here", attacker),
                               (getattr(defender, "location", None),
                                "on_challenged_here", defender)):
        if loc_uid is None:
            continue
        loc = g.locs.get(loc_uid)
        if loc is None:
            continue
        ents = entries_for(loc.card.name, trig)
        if ents:
            _run(g, loc.owner,
                 {"card": loc.card, "char": who, "source": loc, "loc": loc},
                 ents)
            if g.winner is not None:
                return


def static_location_keyword(g, loc, kw):
    """A keyword the location itself has right now (Game Preserve)."""
    for e in entries_for(loc.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") != "static_location_keyword":
            continue
        if eff.get("keyword", "").lower() != kw.lower():
            continue
        if check_condition(g, loc.owner, {"card": loc.card, "loc": loc},
                           e.get("condition")):
            return True
    return False


def location_free_move_for(g, loc, ch):
    """Does this character move to this location for free?
    (Pizza Planet: your Toy characters can move here for free.)"""
    for e in entries_for(loc.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") != "free_move_here":
            continue
        want = eff.get("classification")
        if want and want not in ch.card.classifications:
            continue
        if ch.owner == loc.owner:
            return True
    return False


def dispatch_banishes_in_challenge(g, attacker, defender):
    """'Whenever this character banishes another character in a challenge'
    (Raya - Headstrong)."""
    ents = entries_for(attacker.card.name, "on_banishes_in_challenge")
    if ents:
        _run(g, attacker.owner,
             {"card": attacker.card, "char": defender, "source": attacker},
             ents)


def dispatch_challenged_banished(g, defender, attacker):
    """'When this character is challenged and banished' (Bellwether)."""
    ents = entries_for(defender.card.name, "on_challenged_banished")
    if ents:
        _run(g, defender.owner,
             {"card": defender.card, "char": attacker, "source": defender},
             ents)


def static_enters_damage(card):
    """Damage a character enters play with (Zeus - Defiant God)."""
    for e in entries_for(card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") == "enters_with_damage":
            return eff.get("amount", 0)
    return 0


def static_enters_exerted(card):
    """Does this permanent enter play exerted? (Potato, Vine Pod)"""
    for e in entries_for(card.name, "static"):
        if e.get("effect", {}).get("type") == "enters_exerted":
            return True
    return False


def dispatch_opposing_challenge(g, attacker):
    """'Whenever an opposing character challenges' watchers, on the
    non-attacking side (Merida - Gifted Archer FIERCE PROTECTION)."""
    p = 1 - attacker.owner
    for src in list(g.my_chars(p)):
        ents = entries_for(src.card.name, "on_opposing_challenge")
        if not ents:
            continue
        _run(g, p, {"card": src.card, "char": attacker, "source": src}, ents)
        if g.winner is not None:
            return


def dispatch_chosen_by_opponent(g, ch):
    """'Whenever an opponent chooses this character for an action or ability'
    (Flynn Rider - High-Climbing Rogue WE CAN WORK THIS OUT).

    Hooked into abilities._best_opp_char, the single chokepoint every "chosen
    opposing character" effect goes through in both the hand-written Python
    abilities and the schema."""
    ents = entries_for(ch.card.name, "on_chosen_by_opponent")
    if ents:
        _run(g, ch.owner, {"card": ch.card, "char": ch, "source": ch}, ents)


def dispatch_banish(g, ch, cause="damage"):
    """'When this character is banished' triggers. Called from
    abilities.on_banish, by which point the character is already off the
    board, so conditions read the post-banish state."""
    ents = entries_for(ch.card.name, "on_banish")
    if ents:
        _run(g, ch.owner, {"card": ch.card, "char": ch, "source": ch}, ents)


def dispatch_quest(g, ch):
    ents = entries_for(ch.card.name, "on_quest")
    if ents:
        _run(g, ch.owner, {"card": ch.card, "char": ch}, ents)


# ---------------------------------------------------------------------
# Static (continuous) abilities: read by derived-stat functions, not
# dispatched at an event. Returns the numeric contribution or 0.
# ---------------------------------------------------------------------
def static_self_lore(g, ch):
    return static_self_stat(g, ch, "lore")


def static_self_stat(g, ch, stat):
    """Sum of conditional self-buffs on this character for one stat.

    "static_self_lore" is kept as an alias for stat == "lore" so entries
    already promoted into abilities_manual.json keep working.
    """
    total = 0
    aliases = {"static_self_stat"}
    if stat == "lore":
        aliases.add("static_self_lore")
    for e in entries_for(ch.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") not in aliases:
            continue
        if eff.get("type") == "static_self_stat" and eff.get("stat") != stat:
            continue
        if not check_condition(g, ch.owner, {"card": ch.card, "char": ch},
                               e.get("condition")):
            continue
        if eff.get("per") == "cards_in_opponent_hands":
            total += eff.get("amount", 0) * len(g.players[1 - ch.owner].hand)
        elif eff.get("per") == "cards_under":
            total += eff.get("amount", 0) * (len(getattr(ch, "boosted", []))
                                             + len(getattr(ch, "under", [])))
        else:
            total += eff.get("amount", 0)
    return total


def strength_floor(g, ch):
    """Does a permanent you control stop this character's Strength being
    reduced below its printed value (Elisa Maza - Transformed Gargoyle)?"""
    for src in list(g.my_chars(ch.owner)) + list(g.items[ch.owner]) \
            + list(g.my_locs(ch.owner)):
        for e in entries_for(src.card.name, "static"):
            eff = e.get("effect", {})
            if eff.get("type") != "team_strength_floor":
                continue
            if check_condition(g, src.owner,
                               {"card": src.card,
                                "char": src if _obj_is_char(src) else None},
                               e.get("condition")):
                return True
    return False


def static_no_ready(g, ch):
    """Is this character prevented from readying right now?

    Distinct from the one-shot {"kind": "no_ready"} effect the engine already
    consumes at the ready step: this is a standing restriction re-evaluated
    every turn, so it must not be consumed.
    """
    for e in entries_for(ch.card.name, "static"):
        if e.get("effect", {}).get("type") != "static_no_ready":
            continue
        if check_condition(g, ch.owner, {"card": ch.card, "char": ch},
                           e.get("condition")):
            return True
    return False


def static_self_resist(g, ch):
    """Conditional Resist +N granted by a static entry (Omnidroid V.10)."""
    return static_self_stat(g, ch, "resist")


def shift_onto_names(card):
    """Extra base names this card may be shifted ONTO (Tod & Copper - Best of
    Friends shifts onto a character named Tod or Copper)."""
    out = []
    for e in entries_for(card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") == "shift_onto_names":
            out.extend(eff.get("names") or [])
    return out


def shift_aliases(card):
    """Extra base names this card counts as for Shift (Incrediboy SPOILER
    ALERT: 'also counts as being named Syndrome')."""
    out = []
    for e in entries_for(card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") == "shift_alias" and eff.get("name"):
            out.append(eff["name"])
    return out


def location_aura_stat(g, ch, stat):
    """Stat bonus from the location this character is standing at
    (Hidden Cove REVITALIZING WATERS). Applies to either side's characters,
    matching the printed 'Characters get ... while here'."""
    if ch.location is None:
        return 0
    loc = g.locs.get(ch.location)
    if loc is None:
        return 0
    total = 0
    for e in entries_for(loc.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") == "location_aura_stat" and eff.get("stat") == stat:
            total += eff.get("amount", 0)
    return total


def note_card_under(g, p, obj):
    """Record that a card was put under a permanent this turn. Read by
    put_card_under_this_turn; kept separate from the watcher dispatch so it
    also fires for non-Boost sources."""
    g.turn_flags.add(("under_this_turn", p))
    uid = getattr(obj, "uid", None)
    if uid is not None:
        g.turn_flags.add(("under_this_turn", uid))


def dispatch_ally_banished(g, ch):
    """'Whenever one of your OTHER characters is banished' watchers
    (Donald Duck - Fred Honeywell WELL WISHES). ctx["char"] is the banished
    character, so effects can scale on what was under it."""
    p = ch.owner
    # engine.banish_char removes the character before dispatching, so a
    # watcher that includes itself has to be considered separately -- it is
    # no longer in g.my_chars.
    watchers = list(g.my_chars(p)) + [ch]
    for src in watchers:
        ents = entries_for(src.card.name, "on_ally_banished")
        for e in ents:
            # "one of your OTHER characters" is the default; entries that say
            # "a <classification> character" may include the source itself.
            if src.uid == ch.uid and not e.get("include_self"):
                continue
            if src is ch and not e.get("include_self"):
                continue
            want = e.get("banished_classification")
            if want and want not in ch.card.classifications:
                continue
            _run(g, p, {"card": src.card, "char": ch, "source": src}, [e])
            if g.winner is not None:
                return
    # "whenever an opposing character is banished", watched from the far side
    opp = 1 - p
    for src in list(g.my_chars(opp)):
        ents = entries_for(src.card.name, "on_opposing_banished")
        for e in ents:
            want = e.get("banished_classification")
            if want and want not in ch.card.classifications:
                continue
            _run(g, opp, {"card": src.card, "char": ch, "source": src}, [e])
            if g.winner is not None:
                return


def dispatch_location_banished(g, loc):
    """'When this location is banished' (Leviathan's Lair)."""
    ents = entries_for(loc.card.name, "on_banish")
    if ents:
        _run(g, loc.owner,
             {"card": loc.card, "loc": loc, "source": loc}, ents)


def blocks_quest_by_classification(g, ch):
    """A permanent in play stopping characters of a classification from
    questing (Hans - Brazen Manipulator: King and Queen characters can't
    quest). Applies to both sides, as printed."""
    for src in list(g.chars.values()):
        for e in entries_for(src.card.name, "static"):
            eff = e.get("effect", {})
            if eff.get("type") != "classification_cant_quest":
                continue
            if ch.card.classifications & set(eff.get("any_of") or []):
                return True
    return False


def move_discount(g, ch):
    """Ink discount on moving this character to a location
    (Raksha - Fearless Mother). Limited uses are tracked like any other
    n-times-per-turn entry."""
    total = 0
    for e in entries_for(ch.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") != "move_cost_reduction":
            continue
        limit = e.get("uses_per_turn") or 1
        key = ("move_disc", ch.uid)
        used = getattr(g, "use_counts", None) or {}
        if used.get(key, 0) >= limit:
            continue
        total += eff.get("amount", 1)
    return total


def note_move_discount_used(g, ch):
    used = getattr(g, "use_counts", None)
    if used is None:
        used = g.use_counts = {}
    key = ("move_disc", ch.uid)
    used[key] = used.get(key, 0) + 1


def blocks_opponent_play(g, p, card):
    """Is player p forbidden from playing this card by an opposing static
    (Gizmoduck FAIL-SAFE)?"""
    for src in list(g.my_chars(1 - p)):
        for e in entries_for(src.card.name, "static"):
            eff = e.get("effect", {})
            if eff.get("type") != "opponent_cant_play":
                continue
            ct = eff.get("card_type")
            if ct and not _card_matches(card, {"card_type": ct}):
                continue
            if card.cost < eff.get("min_cost", 0):
                continue
            if check_condition(g, src.owner,
                               {"card": src.card, "char": src},
                               e.get("condition")):
                return True
    return False


def blocks_item_ready(g, item, owner):
    """An opposing permanent stopping this player's items readying
    (Vincenzo Santorini - On the Run)."""
    for src in list(g.my_chars(1 - owner)):
        for e in entries_for(src.card.name, "static"):
            if e.get("effect", {}).get("type") == "opposing_items_cant_ready":
                return True
    return False


def takes_no_challenge_damage(g, ch):
    """Immunity to challenge damage (Mulan - Standing Her Ground)."""
    for e in entries_for(ch.card.name, "static"):
        if e.get("effect", {}).get("type") != "no_challenge_damage":
            continue
        if check_condition(g, ch.owner, {"card": ch.card, "char": ch},
                           e.get("condition")):
            return True
    return False


def blocks_quest_challenge(g, ch):
    """Static restrictions on questing/challenging (Willie the Giant: can't
    do either unless you put a card under him this turn)."""
    for e in entries_for(ch.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") != "no_quest_or_challenge_unless":
            continue
        if not check_condition(g, ch.owner, {"card": ch.card, "char": ch},
                               e.get("condition")):
            return True
    return False


def dispatch_card_under(g, p, obj, via_boost):
    """'Whenever you put a card under this character' (Little John) and
    'whenever you use the Boost ability of a character' (Donald Duck - Fred
    Honeywell). Called from the single place cards go under a permanent."""
    ents = entries_for(obj.card.name, "on_card_under_self")
    if ents:
        _run(g, p, {"card": obj.card,
                    "char": obj if _obj_is_char(obj) else None,
                    "source": obj}, ents)
    # watchers that fire on any card going under one of your permanents,
    # not only on a Boost activation (Ares - God of War)
    for src in list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p)):
        if getattr(src, "uid", None) == getattr(obj, "uid", None):
            continue
        ents = entries_for(src.card.name, "on_any_card_under")
        if ents:
            _run(g, p, {"card": src.card, "char": obj, "source": src}, ents)
            if g.winner is not None:
                return
    if not via_boost:
        return
    for src in list(g.my_chars(p)) + list(g.items[p]):
        if src.uid == obj.uid:
            continue
        ents = entries_for(src.card.name, "on_boost_used")
        if ents:
            _run(g, p, {"card": src.card, "char": obj, "source": src}, ents)


def dispatch_ally_challenges(g, attacker, defender):
    """'Whenever one of your characters challenges another character'
    watchers on your own board (Shere Khan - Menacing Predator, Queen of
    Hearts - Sensing Weakness, Goliath - Guardian of Castle Wyvern).

    ctx["char"] is the defender, so effects can read what was challenged.
    """
    p = attacker.owner
    for src in list(g.my_chars(p)):
        ents = entries_for(src.card.name, "on_ally_challenges")
        for e in ents:
            want = e.get("attacker_classification")
            if want and want not in attacker.card.classifications:
                continue
            _run(g, p, {"card": src.card, "char": defender, "source": src},
                 [e])
            if g.winner is not None:
                return


def dispatch_challenges(g, attacker, defender=None):
    """'Whenever this character challenges another character'
    (Captain Hook - Conniving Pirate)."""
    ents = entries_for(attacker.card.name, "on_challenges")
    for e in ents:
        # "challenges a character with N Strength or less" gates on the
        # defender (Brom Bones - Burly Bully).
        cap = e.get("defender_max_strength")
        if cap is not None:
            if defender is None or g.eff_strength(defender) > cap:
                continue
        _run(g, attacker.owner,
             {"card": attacker.card, "char": attacker, "source": attacker,
              "defender": defender}, [e])
        if g.winner is not None:
            return


def team_static_keyword(g, ch, kw):
    """Is this keyword granted to this character by a permanent you control?
    Boolean form, for keywords with no numeric value."""
    return team_static_keyword_amount(g, ch, kw) is not None


def team_static_keyword_amount(g, ch, kw):
    """The summed value of a granted numeric keyword (Resist +N,
    Challenger +N), or None when nothing grants it.

    Returns a value rather than a bool because resist() and
    challenger_bonus() sum by amount -- a boolean grant would resolve to +0
    and the card would look implemented while doing nothing.

    Scans your locations as well as your characters, so a location can grant
    a keyword to your team (Beast's Castle - Overrun by the Vine).
    """
    total = None
    for src in list(g.my_chars(ch.owner)) + list(g.my_locs(ch.owner)):
        if getattr(src, "uid", None) == ch.uid \
                and not any(e.get("effect", {}).get("include_self")
                            for e in entries_for(src.card.name, "static")):
            continue
        for e in entries_for(src.card.name, "static"):
            eff = e.get("effect", {})
            if eff.get("type") != "team_keyword":
                continue
            if eff.get("keyword", "").lower() != kw.lower():
                continue
            want = eff.get("classification")
            if want and want not in ch.card.classifications:
                continue
            name = eff.get("name")
            if name and ch.card.base_name != name:
                continue
            if eff.get("exerted_only") and not ch.exerted:
                continue
            if check_condition(g, src.owner,
                               {"card": src.card, "char": src},
                               e.get("condition")):
                total = (total or 0) + eff.get("amount", 0)
    return total


def team_static_stat(g, ch, stat):
    """A stat bonus granted to your OTHER characters (Genie - Of the Lamp)."""
    total = 0
    for src in g.my_chars(ch.owner):
        for e in entries_for(src.card.name, "static"):
            eff = e.get("effect", {})
            if eff.get("type") != "team_stat" or eff.get("stat") != stat:
                continue
            if src.uid == ch.uid and not eff.get("include_self"):
                continue
            want = eff.get("classification")
            if want and want not in ch.card.classifications:
                continue
            if check_condition(g, src.owner,
                               {"card": src.card, "char": src},
                               e.get("condition")):
                total += eff.get("amount", 0)
    return total


def static_self_keyword(g, ch, kw):
    """Does a static entry grant this character the named keyword right now?
    (Ursula - Whisper of Vanessa gains Evasive while boosted.)"""
    for e in entries_for(ch.card.name, "static"):
        eff = e.get("effect", {})
        if eff.get("type") != "static_self_keyword":
            continue
        if eff.get("keyword", "").lower() != kw.lower():
            continue
        if check_condition(g, ch.owner, {"card": ch.card, "char": ch},
                           e.get("condition")):
            return True
    return False


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


from . import abilities  # noqa: E402
