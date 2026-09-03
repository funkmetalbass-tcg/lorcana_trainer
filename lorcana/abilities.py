"""Card-specific rules for the two decks, plus documented heuristics for
'chosen ...' triggers that are auto-resolved rather than exposed as decisions.

Phase 1 (generalization): standard keywords -- Bodyguard, Evasive, Rush, Ward,
Reckless, Support, Resist +N, Challenger +N, Singer N, Shift N, Sing Together N
-- are read generically from Card.keywords (parsed from printed text), so any
card in the master JSON gets them with no per-card code. Named abilities remain
hand-coded here (Phase 2 migrates them to a data schema; see schema.py).

Every function that embodies a judgment call is listed in ASSUMPTIONS at the
bottom so the user can audit them.
"""


# ---------------------------------------------------------------------
# Card-text parsers are memoized by card name.
#
# Card text is immutable after load, but these are called for every card in
# hand and every permanent on board at every node of the search, so the regex
# work dominated the profile (~1.4M re.search calls, ~13% of runtime).
# keywords.py already parses printed keywords once at load; these helpers
# predate that and never got the same treatment.
#
# Keyed by card.name rather than id(card): Card objects are shared per name via
# CardDB, but a name key stays correct even if a second DB is loaded.
# ---------------------------------------------------------------------
_CARD_TEXT_MEMO = {}


def _memo_by_card(fn):
    cache = _CARD_TEXT_MEMO.setdefault(fn.__name__, {})

    def wrapper(card):
        name = card.name
        try:
            return cache[name]
        except KeyError:
            v = cache[name] = fn(card)
            return v
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__wrapped__ = fn
    return wrapper


_COMBO_RE = None


@_memo_by_card
def combo_shift_cost(card):
    """Printed 'Combo Shift N Ink' -> N, else None.

    Combo Shift lets the card shift onto a character named after EITHER half of
    its combo name (Sulley & Boo -> a 'Sulley' or a 'Boo'). The generic keyword
    parser deliberately rejects it ('Combo' residue), so it is handled here.
    """
    global _COMBO_RE
    if _COMBO_RE is None:
        import re as _re
        _COMBO_RE = _re.compile(r"\bCombo Shift\s+(\d+)\s*Ink\b")
    m = _COMBO_RE.search(keywords.clean_text(card.text))
    return int(m.group(1)) if m else None


def combo_shift_names(card):
    """Base names a Combo Shift card may shift onto."""
    return [n.strip() for n in card.base_name.split("&") if n.strip()]


_DUO_RE = None
_TEMP_RE = None


@_memo_by_card
def duo_shift_cost(card):
    """'Duo Shift N' -> N. Plays on top of TWO characters named after each half
    of the combo name (Mickey Mouse & Minnie Mouse)."""
    global _DUO_RE
    if _DUO_RE is None:
        import re as _re
        _DUO_RE = _re.compile(r"\bDuo Shift\s+(\d+)")
    m = _DUO_RE.search(keywords.clean_text(card.text))
    return int(m.group(1)) if m else None


@_memo_by_card
def temporary_shift_cost(card):
    """'Temporary Shift N' -> N. Like Shift, but the character returns to its
    previous form at the end of the turn."""
    global _TEMP_RE
    if _TEMP_RE is None:
        import re as _re
        _TEMP_RE = _re.compile(
            r"\bTemporary(?:\s+[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)?)?"
            r"\s+Shift\s+(\d+)")
    m = _TEMP_RE.search(keywords.clean_text(card.text))
    return int(m.group(1)) if m else None


_TEMP_CLS_RE = None


def temporary_shift_classification(card):
    """The classification named by a "Temporary <Class> Shift N" variant, or
    None for the plain same-name form."""
    global _TEMP_CLS_RE
    if _TEMP_CLS_RE is None:
        import re as _re
        _TEMP_CLS_RE = _re.compile(
            r"\bTemporary\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)?)\s+Shift\s+\d+")
    m = _TEMP_CLS_RE.search(keywords.clean_text(card.text))
    return m.group(1) if m else None


@_memo_by_card
def shift_cost(card):
    """Printed 'Shift N' cost (generic, Phase 1), or a Shift variant."""
    if card.shift_ink is not None:
        return card.shift_ink
    for fn in (combo_shift_cost, duo_shift_cost, temporary_shift_cost):
        v = fn(card)
        if v is not None:
            return v
    return None


_BOOST_RE = None


@_memo_by_card
def boost_cost(card):
    """Printed 'Boost N Ink' -> N, else None. Generic: read from card text."""
    global _BOOST_RE
    if _BOOST_RE is None:
        import re as _re
        _BOOST_RE = _re.compile(r"\bBoost\s+(\d+)\s*Ink\b")
    m = _BOOST_RE.search(keywords.clean_text(card.text))
    return int(m.group(1)) if m else None


# =====================================================================
# Derived stats
# =====================================================================
def strength(g, ch):
    s = ch.card.strength or 0
    s += schema.static_self_stat(g, ch, "str")
    s += schema.team_static_stat(g, ch, "str")
    s += schema.location_aura_stat(g, ch, "str")
    name = ch.card.name
    # MAGICAL MIX: +1 for each different ink type among your characters
    if name == "Winnie The Pooh & Piglet - Hunny Mages":
        inks = set()
        for c in g.my_chars(ch.owner):
            for i in str(c.card.ink_type).split(";"):
                i = i.strip()
                if i:
                    inks.add(i)
        s += len(inks)
    # COUNTING COINS: +1 str/+1 will for each card under him
    elif name == "Scrooge McDuck - Ghostly Ebenezer":
        s += len(ch.boosted)
    # SUPERHUMAN STRENGTH: +3 while there's a card under him
    elif name == "Hercules - Spectral Demigod" and ch.boosted:
        s += 3
    # Mr. Incredible ALWAYS UNITED: +2 str for each OTHER character you have
    if name == "Mr. Incredible - Super Strong":
        s += 2 * max(0, len(g.my_chars(ch.owner)) - 1)
    if name == "Alien - True Believer":
        s += sum(1 for c in g.my_chars(ch.owner)
                 if c.uid != ch.uid and c.card.is_toy)
    # Lumiere: your OTHER characters +1 str
    for c in g.my_chars(ch.owner):
        if c.card.name == "Lumiere - Fiery Friend" and c.uid != ch.uid:
            s += 1
    # Snow Fort THE HIGH GROUND: your characters get +1 Strength.
    for it in g.items[ch.owner]:
        if it.card.name == "Snow Fort":
            s += 1
            break
    # temp effects
    for e in g.effects:
        if e["kind"] == "str" and e["target"] == ch.uid:
            s += e["amount"]
    return s


def willpower(g, ch):
    w = ch.card.willpower or 0
    w += schema.static_self_stat(g, ch, "will")
    w += schema.location_aura_stat(g, ch, "will")
    if ch.location is not None:
        loc = g.locs.get(ch.location)
        if loc and loc.card.name == "Hundred Acre Wood - Hunny Campsite":
            w += 1
    if ch.card.name == "Scrooge McDuck - Ghostly Ebenezer":
        w += len(ch.boosted)
    # Woody Jungle Guide: your OTHER Toy characters +1 willpower
    for c in g.my_chars(ch.owner):
        if c.card.name == "Woody - Jungle Guide" and c.uid != ch.uid and ch.card.is_toy:
            w += 1
    return w


def lore(g, ch):
    l = ch.card.lore
    name = ch.card.name
    if name == "The Queen - Devious Disguise":
        if g.players[1 - ch.owner].lore > g.players[ch.owner].lore:
            l += 2
    if name == "John Silver - Greedy Treasure Seeker":
        l += len(g.my_locs(ch.owner))
    if name == "Elsa - Ice Artisan" and ch.location is not None:
        l += 3
    # Lilo CREATIVE INSPIRATION: +1 lore while you have a Stitch in play
    if name == "Lilo - Snow Artist" and any(
            c.card.base_name == "Stitch" for c in g.my_chars(ch.owner)):
        l += 1
    # Campsite HUNNY QUEST: Hunny characters get +1 lore while here
    if ch.location is not None:
        loc = g.locs.get(ch.location)
        if loc and loc.card.name == "Hundred Acre Wood - Hunny Campsite" \
                and is_hunny(g, ch):
            l += 1
    # STICK TOGETHER: while you have 2+ OTHER Hunny characters, +2 lore
    if name == "Winnie The Pooh - Hunny Archmage" and _hunny_count(g, ch.owner, ch.uid) >= 2:
        l += 2
    # Nala DETERMINED DIVERSION: +1 Lore while she has no damage.
    if name == "Nala - Undaunted Lioness" and ch.damage == 0:
        l += 1
    # Megara I'LL BE FINE: +1 Lore while there's a card under her.
    if name == "Megara - Secret Keeper" and ch.boosted:
        l += 1
    # Anna - Braving the Storm I WAS BORN READY: +1 Lore while you have
    # another Hero character in play.
    if name == "Anna - Braving the Storm":
        if any("Hero" in c.card.classifications and c.uid != ch.uid
               for c in g.my_chars(ch.owner)):
            l += 1
    # Alice - Growing Girl WHAT DID I DO?: +4 Lore while 10+ Strength.
    if name == "Alice - Growing Girl" and g.eff_strength(ch) >= 10:
        l += 4
    # schema-driven static self-lore (e.g. "While you have X, +N Lore")
    l += schema.static_self_lore(g, ch)
    for e in g.effects:
        if e["kind"] == "lore" and e["target"] == ch.uid:
            l += e["amount"]
    return l


def resist(g, ch):
    from . import schema
    r = schema.static_self_resist(g, ch)
    kwv = ch.card.kw("Resist")
    if isinstance(kwv, int):
        r += kwv  # printed Resist +N (generic, Phase 1)
    # Angel UNTOUCHABLE: Resist +2 while you have no cards in hand
    if ch.card.name == "Angel - Experiment 624" and not g.players[ch.owner].hand:
        r += 2
    # effect-granted Resist (We'll Save Our Village)
    for e in g.effects:
        if e["kind"] == "resist" and e["target"] == ch.uid:
            r += e["amount"]
    # Nala DETERMINED DIVERSION: Resist +1 while she has no damage.
    if ch.card.name == "Nala - Undaunted Lioness" and ch.damage == 0:
        r += 1
    # Snow Fort BARRICADE: during opponents' turns, your characters get
    # Resist +1.
    if g.active != ch.owner:
        for it in g.items[ch.owner]:
            if it.card.name == "Snow Fort":
                r += 1
                break
    # Mrs. Incredible PULL BACK: your characters gain Resist +1
    for c in g.my_chars(ch.owner):
        if c.card.name == "Mrs. Incredible - Determined Rescuer":
            r += 1
            break
    # Judy LATERAL THINKING: your Detectives get Resist +2 during your turn
    if g.active == ch.owner and has_classification(g, ch, "Detective"):
        for c in g.my_chars(ch.owner):
            if c.card.name == "Judy Hopps - Lead Detective":
                r += 2
                break
    # Owl HUNNY ALLIANCE: Resist +2 while you have another Hunny character
    if ch.card.name == "Owl - Hunny Ranger" and _hunny_count(g, ch.owner, ch.uid) >= 1:
        r += 2
    # Gopher FORTIFYING MEAL: during an OPPONENT'S turn, while Gopher is
    # exerted, your OTHER Hunny characters gain Resist +1
    if is_hunny(g, ch) and g.active != ch.owner:
        for c in g.my_chars(ch.owner):
            if c.card.name == "Gopher - Hunny Cook" and c.exerted and c.uid != ch.uid:
                r += 1
    if ch.card.name == "John Silver - Greedy Treasure Seeker":
        r += len(g.my_locs(ch.owner))
    loc = g.locs.get(ch.location)
    if loc and loc.card.name == "Castle Wyvern - Above the Clouds":
        r += 1
    return r


def challenge_damage(g, ch):
    """Damage a character deals during a challenge. Normally Strength, but
    Dale's SPIKE SUIT makes your characters deal damage with Willpower."""
    if any(c.card.name == "Dale - Ready for His Shot" for c in g.my_chars(ch.owner)):
        return g.eff_willpower(ch)
    return g.eff_strength(ch)


challenge_power = challenge_damage   # back-compat alias


def attacker_takes_no_challenge_damage(g, attacker, defender):
    """The attacker takes no damage from this challenge."""
    # Beast DYNAMIC MANEUVER: while at a location
    if attacker.card.name == "Beast - Snowfield Troublemaker" \
            and attacker.location is not None:
        return True
    # Rafiki ANCIENT SKILLS: when challenging a Hyena character
    if attacker.card.name == "Rafiki - Mystical Fighter" \
            and has_classification(g, defender, "Hyena"):
        return True
    return False


def can_challenge_ready(g, attacker, defender):
    """May `attacker` challenge the READY `defender`?"""
    # Darkwing EVILDOERS BEWARE!: can challenge ready Villain characters
    if attacker.card.name == "Darkwing Duck - Cool Under Pressure" \
            and has_classification(g, defender, "Villain"):
        return True
    # One Last Hope: chosen Hero may challenge ready characters this turn
    return any(e["kind"] == "challenge_ready" and e["target"] == attacker.uid
               for e in g.effects)


def challenger_bonus(g, ch):
    b = 0
    kwv = ch.card.kw("Challenger")
    if isinstance(kwv, int):
        b += kwv  # printed Challenger +N (generic, Phase 1)
    for e in g.effects:
        if e["kind"] == "challenger" and e["target"] == ch.uid:
            b += e["amount"]
    loc = g.locs.get(ch.location)
    if loc and loc.card.name == "Castle Wyvern - Above the Clouds":
        b += 1
    return b


def has_classification(g, ch, what):
    """Classification, printed or granted."""
    if what in ch.card.classifications:
        return True
    # The Thunderquack VIGILANTE JUSTICE: all opposing characters gain Villain
    if what == "Villain":
        for it in g.items[1 - ch.owner]:
            if it.card.name == "The Thunderquack":
                return True
    return any(e["kind"] == "classification" and e["target"] == ch.uid
               and e.get("what") == what for e in g.effects)


def is_hunny(g, ch):
    """Hunny classification, printed or granted (Magical Hunny Staff)."""
    return has_classification(g, ch, "Hunny")


def _hunny_count(g, p, exclude_uid=None):
    return sum(1 for c in g.my_chars(p)
               if c.uid != exclude_uid and is_hunny(g, c))


def has_evasive(g, ch):
    if ch.card.kw("Evasive"):
        return True
    # NOT A FLYING TOY (Buzz Lightyear - Grounded): printed Evasive would
    # still count, but this character can never be *granted* Evasive.
    if schema.static_self_keyword(g, ch, "cant_gain_evasive"):
        return False
    # Roo ELUSIVE EXPERTISE: gains Evasive while you have another Hunny
    if ch.card.name == "Roo - Hunny Rogue" and _hunny_count(g, ch.owner, ch.uid) >= 1:
        return True
    # Peter Pan & Tinker Bell YOU CAN FLY!: your characters gain Evasive
    for c in g.my_chars(ch.owner):
        if c.card.name == "Peter Pan & Tinker Bell - Fast Friends":
            return True
    if schema.static_self_keyword(g, ch, "evasive"):
        return True
    return any(e["kind"] == "evasive" and e["target"] == ch.uid for e in g.effects)


def has_alert(g, ch):
    """Alert: this character can challenge Evasive characters. It does NOT grant
    Evasive to the character itself (they remain challengeable normally)."""
    if ch.card.kw("Alert"):
        return True
    # prose-granted Alert (Angus, Minnie Mouse - Ghost Hunter, ...)
    if any(e["kind"] == "alert" and e["target"] == ch.uid for e in g.effects):
        return True
    # Judy Hopps LATERAL THINKING: during your turn, your Detective characters
    # gain Alert and Resist +2
    if g.active == ch.owner and has_classification(g, ch, "Detective"):
        for c in g.my_chars(ch.owner):
            if c.card.name == "Judy Hopps - Lead Detective":
                return True
    return False


def can_challenge_evasive(g, ch):
    """An attacker may challenge Evasive defenders if it has Evasive or Alert."""
    return has_evasive(g, ch) or has_alert(g, ch)


def has_rush(g, ch):
    if ch.card.kw("Rush"):
        return True
    from . import schema
    if schema.static_self_keyword(g, ch, "rush") \
            or schema.team_static_keyword(g, ch, "rush"):
        return True
    return any(e["kind"] == "rush" and e["target"] == ch.uid
               for e in g.effects)


def has_bodyguard(g, ch):
    return bool(ch.card.kw("Bodyguard"))


def _is_emerald(card):
    return "Emerald" in str(card.ink_type)


def has_ward(g, ch):
    # Aurora - Dreaming Guardian PROTECTIVE EMBRACE: your OTHER characters
    # gain Ward.
    for c in g.my_chars(ch.owner):
        if c.card.name == "Aurora - Dreaming Guardian" and c.uid != ch.uid:
            return True
    from . import schema
    if schema.static_self_keyword(g, ch, "ward") \
            or schema.team_static_keyword(g, ch, "ward"):
        return True
    return _has_ward_base(g, ch)


def _has_ward_base(g, ch):
    """Ward: opponents can't choose this character with effects (challenges
    and non-targeted mass effects still apply). May also be granted by an
    effect (e.g. Eeyore's HUNNYTACTICS)."""
    if ch.card.kw("Ward"):
        return True
    # Goofy PROVIDE COVER: your other Emerald characters gain Ward
    if _is_emerald(ch.card):
        for g2 in g.my_chars(ch.owner):
            if g2.card.name == "Goofy - Emerald Champion" and g2.uid != ch.uid:
                return True
    return any(e["kind"] == "ward" and e["target"] == ch.uid for e in g.effects)


def action_ink_surcharge(g, ch):
    """Extra ink that must be paid each time this character quests or
    challenges. RC - Remote-Controlled Car LOW BATTERIES: can't quest or
    challenge unless you pay 1 Ink (each time)."""
    if ch.card.name == "RC - Remote-Controlled Car":
        return 1
    return 0


def cant_challenge(g, ch):
    """Characters forbidden from challenging (Chief Powhatan STANDS HIS GROUND),
    or while a global challenge lock is active (Pocahontas - Peacekeeper
    CALMING WORDS)."""
    if ch.card.name == "Chief Powhatan - Protective Leader":
        return True
    if any(e.get("kind") == "challenge_lock" for e in g.effects):
        return True
    return False


def has_reckless(g, ch):
    """Reckless: can't quest; must challenge each turn if able.

    Also consults g.effects so prose-granted Reckless works (Potion of Malice
    MINDLESS RAGE). Mirrors has_evasive; without this a grant_keyword entry
    naming "reckless" would be written and then silently ignored.
    """
    if ch.card.kw("Reckless"):
        return True
    return any(e["kind"] == "reckless" and e["target"] == ch.uid
               for e in g.effects)


def has_support(g, ch):
    if ch.card.kw("Support"):
        return True
    # Alice - Growing Girl GOOD ADVICE: your OTHER characters gain Support.
    for c in g.my_chars(ch.owner):
        if c.card.name == "Alice - Growing Girl" and c.uid != ch.uid:
            return True
    # Minnie Mouse - Sweetheart Princess ROYAL FAVOR: your Mickey Mouse
    # characters gain Support.
    if ch.card.base_name == "Mickey Mouse":
        if any(c.card.name == "Minnie Mouse - Sweetheart Princess"
               for c in g.my_chars(ch.owner)):
            return True
    return any(it.card.name == "Ranger Plane" for it in g.items[ch.owner])


def _free_play_cost1_from_hand(g, p, why, chosen=None, allow_decline=False):
    """Free-play a cost-1 character from hand. If `chosen` names a card in
    hand, play that one (the searchable choice); otherwise fall back to the
    best cost-1 by (lore, strength). If `allow_decline` and chosen is None,
    the choice was an explicit decline -- do nothing.
    Returns True if a card was played."""
    cands = [c for c in g.players[p].hand if c.is_character and c.cost == 1]
    if not cands:
        return False
    best = None
    if chosen is not None:
        best = next((c for c in cands if c.name == chosen), None)
    if best is None:
        if allow_decline:
            return False
        best = max(cands, key=lambda c: (c.lore, c.strength or 0))
    g.emit(f"{why} free-plays {best.name}")
    g._play_card(p, best, {}, free=True)
    return True


def can_enter_exerted(card):
    # Bodyguard's rules text allows entering play exerted; Mickey's LONG
    # JOURNEY and Gopher's DOWN THE HOLE are named abilities granting the same.
    return (bool(card.kw("Bodyguard"))
            or card.name in ("Mickey Mouse - Expedition Leader",
                             "Gopher - Hunny Cook",
                             "Merida - Wisp Conjurer"))


def location_lore(g, loc):
    from . import schema
    _schema_bonus = schema.static_location_lore(g, loc)
    l = loc.card.lore
    if loc.card.name == "Illuminary Tunnels - Linked Caverns":
        if any(c.location == loc.uid for c in g.my_chars(loc.owner)):
            l += sum(1 for o in g.my_locs(loc.owner) if o.uid != loc.uid)
    # GOOD BUSINESS: +1 Willpower and +1 Lore for each card under it
    if loc.card.name == "Scrooge's Counting House - Ebenezer's Office":
        l += len(loc.under)
    return l + _schema_bonus


def location_willpower(g, loc):
    w = loc.card.willpower or 0
    if loc.card.name == "Scrooge's Counting House - Ebenezer's Office":
        w += len(loc.under)
    return w


def location_resist(g, loc):
    """Launchpad STAND GUARD: your locations gain Resist +1. Also picks up any
    schema-parsed 'your locations gain Resist +N' abilities generically."""
    r = 0
    for c in g.my_chars(loc.owner):
        if c.card.name == "Launchpad - Hideout Defender":
            r += 1
    for e in g.effects:
        if e["kind"] == "resist" and e["target"] == loc.uid:
            r += e["amount"]
    r += schema.static_location_resist(g, loc)
    return r


# =====================================================================
# Cost modifiers
# =====================================================================
def static_discount(g, p, card):
    d = schema.static_free_discount(g, p, card)
    # Liquidator UNDERDOG: if this is your first turn and you're not the first
    # player, you pay 1 ink less. (Global turn counter: P1's first turn is 2.)
    if card.name == "Liquidator - Iced Over" and p == 1 and g.turn <= 2:
        d += 1
    if card.is_character:
        # Grandmother Willow SMOOTH THE WAY: once per turn (per copy), the
        # next character you play costs 1 less
        for c in g.my_chars(p):
            if c.card.name == "Grandmother Willow - Ancient Advisor" \
                    and ("willow", c.uid) not in g.turn_flags:
                d += 1
    if card.name == "Bullseye - Loyal Horse":
        if any(c.card.base_name in ("Woody", "Jessie") for c in g.my_chars(p)):
            d += 1
    if card.is_location:
        # LOCUS: each Illuminary Tunnels with one of your characters there
        for loc in g.my_locs(p):
            if loc.card.name == "Illuminary Tunnels - Linked Caverns" and \
                    any(c.location == loc.uid for c in g.my_chars(p)):
                d += 1
    return d


def discount_applies(d, card):
    f = d["filt"]
    if f == "character":
        return card.is_character
    if f == "action":
        return card.is_action
    if f == "item":
        return card.is_item
    if f == "action_or_item":
        return card.is_action or card.is_item
    if f == "location":
        return card.is_location
    if f == "princess_queen":
        return card.is_character and (
            "Princess" in card.classifications or "Queen" in card.classifications)
    return False


# =====================================================================
# Heuristic target selection (documented simplifications)
# =====================================================================
def _best_opp_char(g, p, cond=lambda g, c: True, key=None, notify=True):
    """Pick an opposing character for a CHOSEN effect. Ward makes a character
    unchoosable by opponents, so warded characters are excluded here. (Mass,
    non-targeted effects like Under the Sea bypass this helper on purpose.)

    notify=False suppresses the "an opponent chooses this character" trigger.
    Pass it when the call is a hypothetical -- a condition testing whether a
    legal target exists, or a policy scoring a move -- rather than an actual
    choice being made."""
    opts = [c for c in g.my_chars(1 - p)
            if cond(g, c) and not has_ward(g, c)]
    if not opts:
        return None
    if key is None:
        key = lambda c: (g.eff_lore(c), g.eff_strength(c))
    pick = max(opts, key=key)
    # "Whenever an opponent chooses this character for an action or ability"
    # (Flynn Rider - High-Climbing Rogue). This helper is the one place every
    # chosen-opposing-character effect resolves its target, so hooking it here
    # covers the Python abilities and the schema alike. Guarded against
    # re-entry: the trigger itself must not re-run while resolving.
    if notify and not getattr(g, "_in_chosen_trigger", False):
        from . import schema
        g._in_chosen_trigger = True
        try:
            schema.dispatch_chosen_by_opponent(g, pick)
        finally:
            g._in_chosen_trigger = False
    return pick


def _debuff_target(g, p):
    # -X strength debuffs: aim at the strongest opposing character
    return _best_opp_char(g, p, key=lambda c: (g.eff_strength(c), g.eff_lore(c)))


def _worst_hand_card(g, p):
    """Discard heuristic: prefer a location cost<=3 (recoverable via Get to
    Safety!), else the cheapest card in hand."""
    hand = g.players[p].hand
    locs = [c for c in hand if c.is_location and c.cost <= 3]
    if locs:
        return locs[0]
    return min(hand, key=lambda c: c.cost)


def can_ink_from_discard(g, p):
    """Moana - Curious Explorer ANCESTRAL LEGACY: while you control her you may
    ink cards from your discard (still one ink per turn, still only cards with
    the inkwell symbol). Read by the engine's ink action generation."""
    return any(c.card.name == "Moana - Curious Explorer" for c in g.my_chars(p))


# =====================================================================
# Trigger hooks called by the engine
# =====================================================================
def _find_a_friend(g):
    """Mike Wazowski FIND A FRIEND: each player reveals the top card of their
    deck. If it's a character card they may put it into hand (heuristic:
    always do); otherwise it goes to the bottom of their deck."""
    for pl in (0, 1):
        deck = g.players[pl].deck
        if not deck:
            continue
        top = deck.pop()
        if top.is_character:
            g.players[pl].hand.append(top)
            g.emit(f"FIND A FRIEND: P{pl} reveals {top.name} -> hand")
        else:
            deck.insert(0, top)
            g.emit(f"FIND A FRIEND: P{pl} reveals {top.name} -> bottom")


def on_play(g, p, card, obj, params):
    name = card.name
    opp = 1 - p

    # Track Princess plays for Cinderella WHATEVER YOU WISH FOR.
    if card.is_character and "Princess" in card.classifications:
        g.turn_flags.add(("played_princess", p))

    # Mowgli - Man Cub HAVE A BETTER LOOK: when played, chosen opponent
    # reveals their hand and discards a non-character card of THEIR choice.
    # Heuristic (their choice = least painful for them): cheapest non-character.
    if name == "Mowgli - Man Cub":
        nonchars = [c for c in g.players[opp].hand if not c.is_character]
        if nonchars:
            d = min(nonchars, key=lambda c: c.cost)
            g.players[opp].hand.remove(d)
            g.players[opp].discard.append(d)
            g.emit(f"HAVE A BETTER LOOK: P{opp} discards {d.name}")

    # World's Greatest Criminal Mind: banish chosen character with 5 or more
    # Strength.
    if name == "World's Greatest Criminal Mind":
        tgt = _best_opp_char(g, p, cond=lambda g, c: g.eff_strength(c) >= 5)
        if tgt is not None:
            g.emit(f"WORLD'S GREATEST CRIMINAL MIND banishes {tgt.card.base_name}")
            g.banish_char(tgt, cause="ability")

    # Hades - Infernal Schemer IS THERE A DOWNSIDE TO THIS?: when played, you
    # may put chosen opposing character into their player's inkwell facedown.
    # (Same mechanic as Let It Go; Ward-protected characters are unchoosable.
    # Heuristic 'may': always remove the opponent's best character.)
    if name == "Hades - Infernal Schemer":
        tgt = _best_opp_char(g, p)
        if tgt is not None:
            owner = tgt.owner
            del g.chars[tgt.uid]
            g.players[owner].ink_cards.append(tgt.card)
            g.players[owner].ink_total += 1   # enters facedown -> not ink_ready
            for u in tgt.under + tgt.boosted:
                g.players[owner].discard.append(u)
            g.emit(f"IS THERE A DOWNSIDE TO THIS? inkwells {tgt.card.name}")
            g.count("Hades IS THERE A DOWNSIDE", p)

    # Mike Wazowski - Heroic Climber FIND A FRIEND fires on play (and on quest,
    # handled in on_quest).
    if name == "Mike Wazowski - Heroic Climber":
        _find_a_friend(g)

    # Lyle Tiberius Rourke EYE FOR VALUE: when played, you may draw a card,
    # then choose and discard a card.
    if name == "Lyle Tiberius Rourke - Adventurer for Hire":
        g.draw(p, 1)
        if g.players[p].hand:
            d = _worst_hand_card(g, p)
            g.players[p].hand.remove(d)
            g.discard_card(p, d)
            g.emit(f"EYE FOR VALUE: discards {d.name}")

    # Chernabog - Unnatural Force DARK DANCE: when played, you may shuffle
    # chosen opposing character into their deck. If you do, that player may
    # play a character from their discard for free. Heuristic: shuffle away the
    # opponent's most valuable non-Warded character; the opponent's free replay
    # takes their cheapest discard character (least value given back).
    if name == "Chernabog - Unnatural Force":
        tgt = _best_opp_char(g, p)
        if tgt is not None:
            del g.chars[tgt.uid]
            g.players[tgt.owner].discard.extend(getattr(tgt, "under", []))
            g.players[tgt.owner].discard.extend(getattr(tgt, "boosted", []))
            g.players[tgt.owner].deck.append(tgt.card)
            g.rng.shuffle(g.players[tgt.owner].deck)
            g.emit(f"DARK DANCE: shuffles {tgt.card.base_name} into P{tgt.owner}'s deck")
            # opponent MAY replay a character from discard for free
            pool = [c for c in g.players[tgt.owner].discard if c.is_character]
            if pool:
                pick = min(pool, key=lambda c: c.cost)
                g.players[tgt.owner].discard.remove(pick)
                g.emit(f"DARK DANCE: P{tgt.owner} free-plays {pick.name} from discard")
                on_discard_leave(g, tgt.owner, 1)
                g._play_card(tgt.owner, pick, {}, free=True)

    # Kristoff - Icy Explorer HIDDEN DEPTHS: when played, if you have a
    # character named Anna in play, you may put a card from chosen player's
    # discard on the bottom of their deck. Heuristic: target the opponent's
    # discard (deny their best rebuy).
    if name == "Kristoff - Icy Explorer":
        has_anna = any(c.card.base_name == "Anna" for c in g.my_chars(p))
        if has_anna:
            # prefer denying the opponent; fall back to own discard if empty
            if g.players[opp].discard:
                _bottom_from_discard(g, p, opp, "HIDDEN DEPTHS")
            elif g.players[p].discard:
                _bottom_from_discard(g, p, p, "HIDDEN DEPTHS")

    # Aladdin - Doing His Part CLEAR IT OUT: when played, you may pay 1 Ink to
    # banish chosen item. "clearit" param: item uid to banish, or None.
    if name == "Aladdin - Doing His Part":
        target_uid = params.get("clearit")
        if target_uid is not None and g.players[p].ink_ready >= 1:
            it = next((x for pl in (0, 1) for x in g.items[pl]
                       if x.uid == target_uid), None)
            if it is not None:
                g.pay_ink(p, 1)
                g.banish_item(it)
                g.emit(f"CLEAR IT OUT banishes {it.card.base_name}")

    # Rapunzel - Tower Defender THE FATE'S DESIGN: when played, you may choose
    # and discard a card; if you do, return chosen character to hand.
    # "fate_disc": hand card name to discard; "fate_ret": character uid to bounce.
    if name == "Rapunzel - Tower Defender":
        disc_name = params.get("fate_disc")
        ret_uid = params.get("fate_ret")
        disc = next((c for c in g.players[p].hand if c.name == disc_name), None)
        ret = g.chars.get(ret_uid) if ret_uid is not None else None
        if disc is not None and ret is not None and not has_ward(g, ret):
            g.players[p].hand.remove(disc)
            g.discard_card(p, disc)
            del g.chars[ret.uid]
            g.players[ret.owner].discard.extend(getattr(ret, "under", []))
            g.players[ret.owner].discard.extend(getattr(ret, "boosted", []))
            g.players[ret.owner].hand.append(ret.card)
            g.emit(f"THE FATE'S DESIGN: discard {disc.name}, return "
                   f"{ret.card.base_name} to hand")


    # Snowball Fight: each opponent discards a card; if you have an Evasive
    # character in play, gain 1 lore.
    if name == "Snowball Fight":
        if g.players[opp].hand:
            d = _worst_hand_card(g, opp)
            g.players[opp].hand.remove(d)
            g.players[opp].discard.append(d)
            g.emit(f"Snowball Fight: P{opp} discards {d.name}")
        if any(has_evasive(g, c) for c in g.my_chars(p)):
            g.gain_lore(p, 1, "Snowball Fight (Evasive)")

    # You Broke My Smolder: discard your hand, then draw 2 cards.
    if name == "You Broke My Smolder":
        n_disc = len(g.players[p].hand)
        g.players[p].discard.extend(g.players[p].hand)
        g.players[p].hand.clear()
        g.draw(p, 2)
        g.emit(f"You Broke My Smolder: discard {n_disc}, draw 2")

    # Scrooge McDuck - S.H.U.S.H. Agent BACKUP PLAN: when played, draw a card,
    # then choose and discard a card.
    if name == "Scrooge McDuck - S.H.U.S.H. Agent":
        g.draw(p, 1)
        if g.players[p].hand:
            d = _worst_hand_card(g, p)
            g.players[p].hand.remove(d)
            g.players[p].discard.append(d)
            g.emit(f"BACKUP PLAN: discards {d.name}")

    # Ursula - Deceiver YOU'LL NEVER EVEN MISS IT: when played, chosen opponent
    # reveals their hand and discards a song card of your choice (if any).
    if name == "Ursula - Deceiver":
        songs = [c for c in g.players[opp].hand if c.is_song]
        if songs:
            chosen = params.get("song")
            d = next((c for c in songs if c.name == chosen), None)
            if d is None:
                # heuristic fallback: the most expensive song
                d = max(songs, key=lambda c: c.cost)
            g.players[opp].hand.remove(d)
            g.players[opp].discard.append(d)
            g.emit(f"YOU'LL NEVER EVEN MISS IT: P{opp} discards song {d.name}")

    # STAY CLOSE: when you play Pocahontas - Guiding the Tribe, you may play a
    # character with cost 1 for free.
    if name == "Pocahontas - Guiding the Tribe":
        # "free1" present in params => the search made an explicit choice
        # (a card name, or None meaning decline). Absent => heuristic.
        if "free1" in params:
            _free_play_cost1_from_hand(g, p, "STAY CLOSE",
                                       chosen=params.get("free1"),
                                       allow_decline=True)
        else:
            _free_play_cost1_from_hand(g, p, "STAY CLOSE")

    # CALMING WORDS: if you used Shift to play Pocahontas - Peacekeeper and none
    # of your characters challenged this turn, characters can't challenge until
    # the start of your next turn. Lock expires at start of p's next turn
    # (until=p), which covers the opponent's intervening turn.
    if name == "Pocahontas - Peacekeeper":
        used_shift = params.get("shift") is not None
        challenged = ("challenged", p) in g.turn_flags
        if used_shift and not challenged:
            g.effects.append({"kind": "challenge_lock", "target": obj.uid if obj else -1,
                              "amount": 0, "until": p})
            g.emit("CALMING WORDS: characters can't challenge until your next turn")

    # Enter-play-exerted draw triggers (Merida - Wisp Conjurer). Runs for any
    # character entering play exerted, before the card-specific branches below
    # (several of which early-return).
    if card.is_character and obj is not None and getattr(obj, "exerted", False):
        if name == "Merida - Wisp Conjurer":
            g.draw(p, 1)
            g.emit("FOCUSED ENERGY: Merida draws a card")
        for c in g.my_chars(p):
            if c.card.name == "Merida - Wisp Conjurer" and c.uid != obj.uid:
                g.draw(p, 1)
                g.emit("BECKON: Merida draws a card")

    # ===== HUNNY DECK =====
    if name == "Christopher Robin - Hunny Sage":
        # MAGICAL SUMMONS: search deck for a Hunny card -> hand
        pl = g.players[p]
        pool = [c for c in pl.deck if "Hunny" in c.classifications]
        if pool:
            # heuristic: take the most expensive Hunny we can eventually cast
            tgt = max(pool, key=lambda c: (c.cost, c.is_character))
            pl.deck.remove(tgt)
            pl.hand.append(tgt)
            g.rng.shuffle(pl.deck)
            g.emit(f"MAGICAL SUMMONS fetches {tgt.name}")
        return

    if name == "Rabbit - Hunny Paladin":
        # HUNNY AURA: chosen Hunny character gets +1 lore this turn
        mine = [c for c in g.my_chars(p)
                if is_hunny(g, c) and (obj is None or c.uid != obj.uid)]
        if mine:
            tgt = max(mine, key=lambda c: (not c.exerted, g.eff_lore(c)))
            g.effects.append({"kind": "lore", "target": tgt.uid,
                              "amount": 1, "until": "eot"})
            g.emit(f"HUNNY AURA: {tgt.card.base_name} +1 lore")
        return

    if name == "Isis Vanderchill - Ice Queen of St. Canard":
        # CHILL OUT: exert chosen opposing character
        tgt = _best_opp_char(g, p, cond=lambda g, c: not c.exerted)
        if tgt:
            tgt.exerted = True
            g.emit(f"CHILL OUT exerts {tgt.card.base_name}")
        return

    if name == "Demona - Scourge of the Wyvern Clan":
        # AD SAXUM COMMUTATE: exert all opposing characters, then each player
        # with fewer than 3 cards in hand draws until they have 3.
        for c in g.my_chars(opp):
            c.exerted = True
        g.emit("AD SAXUM COMMUTATE exerts all opposing characters")
        for q in (p, opp):
            need = 3 - len(g.players[q].hand)
            if need > 0:
                g.draw(q, need)
        return

    if name == "Let It Go":
        # Put chosen character into their player's inkwell facedown & exerted.
        tgt = _best_opp_char(g, p)
        if tgt:
            owner = tgt.owner
            del g.chars[tgt.uid]
            g.players[owner].ink_cards.append(tgt.card)
            g.players[owner].ink_total += 1   # enters exerted -> not ink_ready
            for u in tgt.under + tgt.boosted:
                g.players[owner].discard.append(u)
            g.emit(f"LET IT GO inkwells {tgt.card.name}")
        return

    if card.is_song:
        for a in g.my_chars(p):
            an = a.card.name
            # Ariel I WANT MORE: draw a card, then choose and discard a card
            if an == "Ariel - Determined Mermaid":
                g.draw(p, 1)
                if g.players[p].hand:
                    d = _worst_hand_card(g, p)
                    g.players[p].hand.remove(d)
                    g.players[p].discard.append(d)
                    g.emit(f"I WANT MORE discards {d.name}")
                    g.count("Ariel: I WANT MORE", p)
            # Ariel INSPIRING VOICE: chosen character gains Evasive
            elif an == "Ariel - Adventurous Collector":
                mine = [c for c in g.my_chars(p)]
                if mine:
                    t = max(mine, key=lambda c: g.eff_lore(c))
                    g.effects.append({"kind": "evasive", "target": t.uid,
                                      "amount": 0, "until": p})
                    g.emit(f"INSPIRING VOICE: {t.card.base_name} gains Evasive")

    # Ariel COMMAND PERFORMANCE: once during your turn, whenever you play a
    # song, if there's a card under this character, you may draw a card.
    if card.is_song:
        for a in g.my_chars(p):
            if a.card.name == "Ariel - Ethereal Voice" and a.boosted \
                    and ("cmdperf", a.uid) not in g.turn_flags:
                g.turn_flags.add(("cmdperf", a.uid))
                g.draw(p, 1)
                g.emit("COMMAND PERFORMANCE draws a card")
                break

    if name == "Performance Review":
        pool = [c for c in g.my_chars(p) if not c.exerted
                and (obj is None or c.uid != obj.uid)]
        if pool:
            tgt = max(pool, key=lambda c: (not g.is_dry(c), g.eff_lore(c)))
            ncards = g.eff_lore(tgt)
            tgt.exerted = True
            g.draw(p, ncards)
            g.emit(f"PERFORMANCE REVIEW exerts {tgt.card.base_name}, draws {ncards}")
        return

    if name == "Distract":
        tgt = _debuff_target(g, p)
        if tgt:
            g.effects.append({"kind": "str", "target": tgt.uid,
                              "amount": -2, "until": "eot"})
            g.emit(f"DISTRACT: {tgt.card.base_name} -2 strength")
        g.draw(p, 1)
        return

    if name == "Come Out and Fight!":
        best, n_under = None, 0
        for c in g.my_chars(opp):
            k = len(c.boosted) + len(c.under)
            if k > n_under:
                best, n_under = ("char", c), k
        for it in g.items[opp]:
            if len(it.under) > n_under:
                best, n_under = ("item", it), len(it.under)
        for l in g.my_locs(opp):
            if len(l.under) > n_under:
                best, n_under = ("loc", l), len(l.under)
        if best is not None:
            kind, o = best
            owner = o.owner
            cards = (o.boosted + o.under) if kind == "char" else o.under
            g.rng.shuffle(cards)
            for c in cards:
                g.players[owner].deck.insert(0, c)
            if kind == "char":
                o.boosted, o.under = [], []
            else:
                o.under = []
            nm = o.card.base_name if kind == "char" else o.card.name
            g.emit(f"COME OUT AND FIGHT! bottoms {len(cards)} card(s) from {nm}")
        g.draw(p, 1)
        return

    # ===== AMBER/AMETHYST =====
    if name == "Hades - Looking for a Deal":
        # WHAT D'YA SAY?: choose an opposing character; draw 2 unless its
        # player bottoms it. Opponent keeps valuable characters (we draw).
        pool = g.my_chars(opp)
        if pool:
            tgt = max(pool, key=lambda c: (g.eff_lore(c), c.card.cost))
            keeps = tgt.card.cost >= 4 or g.eff_lore(tgt) >= 2
            if keeps:
                g.draw(p, 2)
                g.emit(f"WHAT D'YA SAY?: P{opp} keeps {tgt.card.base_name}; draw 2")
            else:
                del g.chars[tgt.uid]
                g.players[opp].deck.insert(0, tgt.card)
                g.players[opp].discard.extend(tgt.under)
                g.players[opp].discard.extend(tgt.boosted)
                g.emit(f"WHAT D'YA SAY?: {tgt.card.base_name} goes to the bottom")
        return

    if name == "Ohana Means Family":
        mine = [c for c in g.my_chars(p) if c.damage > 0]
        if mine:
            tgt = max(mine, key=lambda c: c.damage)
            healed = tgt.damage
            tgt.damage = 0
            g.draw(p, healed)
            g.emit(f"OHANA MEANS FAMILY heals {healed}, draws {healed}")
        return

    # ===== DETECTIVES =====
    if name == "Darkwing's Chair Set":
        # SECRET ENTRANCE: top card of deck -> inkwell facedown & exerted
        pl = g.players[p]
        if pl.deck:
            pl.ink_cards.append(pl.deck.pop())
            pl.ink_total += 1
            g.emit("SECRET ENTRANCE inks the top card")
        return

    if name == "Judy Hopps - On the Case":
        # HIDDEN CLUES: with another Detective, put chosen item into its
        # player's inkwell facedown and exerted.
        others = [c for c in g.my_chars(p)
                  if has_classification(g, c, "Detective")
                  and (obj is None or c.uid != obj.uid)]
        if others and g.items[opp]:
            it = max(g.items[opp], key=lambda i: i.card.cost)
            g.items[opp].remove(it)
            g.players[opp].ink_cards.append(it.card)
            g.players[opp].ink_total += 1
            g.emit(f"HIDDEN CLUES inks {it.card.name}")
        return

    if name == "Judy Hopps - Uncovering Clues":
        _thorough_investigation(g, p)
        return

    if name == "One Last Hope":
        mine = list(g.my_chars(p))
        if mine:
            tgt = max(mine, key=lambda c: (has_classification(g, c, "Hero"),
                                           g.eff_strength(c)))
            g.effects.append({"kind": "resist", "target": tgt.uid,
                              "amount": 2, "until": p})
            if has_classification(g, tgt, "Hero"):
                g.effects.append({"kind": "challenge_ready", "target": tgt.uid,
                                  "amount": 0, "until": "eot"})
            g.emit(f"ONE LAST HOPE: {tgt.card.base_name} Resist +2")
        return

    if name == "The Terror That Flaps in the Night":
        dmg = 3 if any(c.card.base_name == "Darkwing Duck"
                       for c in g.my_chars(p)) else 2
        tgt = _best_opp_char(g, p)
        if tgt:
            g.emit(f"THE TERROR THAT FLAPS deals {dmg}")
            g.deal_damage(tgt, dmg)
        return

    # Pluto MAKE ROOM: whenever you play another Steel character, you may
    # banish chosen item.
    if card.is_character and "Steel" in str(card.ink_type):
        for c in g.my_chars(p):
            if c.card.name == "Pluto - Steel Champion" and \
                    (obj is None or c.uid != obj.uid):
                if g.items[opp]:
                    it = max(g.items[opp], key=lambda i: i.card.cost)
                    g.emit(f"MAKE ROOM banishes {it.card.name}")
                    g.banish_item(it)
                break

    # ===== EMERALD/STEEL PING =====
    if name == "Bobby Zimuruski - Spray Cheese Kid":
        # SO CHEESY: draw a card, then choose and discard a card
        g.draw(p, 1)
        if g.players[p].hand:
            d = _worst_hand_card(g, p)
            g.players[p].hand.remove(d)
            g.discard_card(p, d)
            g.emit(f"SO CHEESY discards {d.name}")
            g.count("Bobby: SO CHEESY", p)
        return

    if name == "Dinky - Has the Brains":
        # GET HIM!: each opponent chooses one of their characters and deals 1
        # damage to them. The opponent chooses -- pick their least-bad option.
        pool = g.my_chars(opp)
        if pool:
            # opponent picks the character that best survives 1 damage
            victim = max(pool, key=lambda c: g.eff_willpower(c) - c.damage)
            g.emit(f"GET HIM!: P{opp} damages {victim.card.base_name}")
            g.count("Dinky: GET HIM!", p)
            g.deal_damage(victim, 1)
        return

    if name == "Tinker Bell - Giant Fairy":
        # ROCK THE BOAT: deal 1 damage to each opposing character
        for c in list(g.my_chars(opp)):
            g.deal_damage(c, 1)
        g.emit("ROCK THE BOAT deals 1 to each opposing character")
        return

    if name == "Chomp!":
        tgt = _best_opp_char(g, p, cond=lambda g, c: c.damage > 0)
        if tgt:
            g.emit(f"CHOMP! deals 2 to {tgt.card.base_name}")
            g.deal_damage(tgt, 2)
        return

    if name == "Look What You've Done":
        tgt = _best_opp_char(g, p)
        if tgt:
            g.deal_damage(tgt, 2)
        return

    if name == "Malicious, Mean, and Scary":
        for c in list(g.my_chars(opp)):
            c.damage += 1
            g.emit(f"MALICIOUS, MEAN, AND SCARY: 1 damage on {c.card.base_name}")
            g.check_banish(c)
        return

    if name == "Strike A Good Match":
        g.draw(p, 2)
        if g.players[p].hand:
            d = _worst_hand_card(g, p)
            g.players[p].hand.remove(d)
            g.discard_card(p, d)
            g.emit(f"STRIKE A GOOD MATCH discards {d.name}")
        return

    if name == "Windstorm":
        for c in list(g.my_chars(opp)):
            g.deal_damage(c, 3 if has_evasive(g, c) else 1)
        for l in list(g.my_locs(opp)):
            g.damage_loc(l, 1)
        g.emit("WINDSTORM sweeps the opposing board")
        return

    # ===== TINK/PAN, LILO/STITCH, MICKEY/MINNIE, RUBY/STEEL SONGS =====
    if name == "Peter Pan - Playful Prankster":
        # STAY RIGHT THERE: chosen opposing character can't ready next turn
        tgt = _best_opp_char(g, p)
        if tgt:
            g.effects.append({"kind": "no_ready", "target": tgt.uid,
                              "amount": 0, "until": None})
            g.emit(f"STAY RIGHT THERE: {tgt.card.base_name} can't ready")
        return

    if name == "Violet Parr - Learning New Powers":
        # DEFLECT: move 1 damage from chosen character to chosen opposing char
        mine = [c for c in g.my_chars(p) if c.damage > 0]
        tgt = _best_opp_char(g, p)
        if mine and tgt:
            src = max(mine, key=lambda c: c.damage)
            src.damage -= 1
            tgt.damage += 1
            g.emit(f"DEFLECT moves 1 damage {src.card.base_name} -> {tgt.card.base_name}")
            g.check_banish(tgt)
        return

    if name == "Sisu - Daring Visitor":
        tgt = _best_opp_char(g, p, cond=lambda g, c: g.eff_strength(c) <= 1)
        if tgt:
            g.emit(f"BRING ON THE HEAT! banishes {tgt.card.base_name}")
            g.banish_char(tgt)
        return

    if name == "Meilin Lee - Losing Control":
        # RED PANDA POWER: look at top 4, reveal a Red Panda or song -> hand
        pl = g.players[p]
        top = [pl.deck.pop() for _ in range(min(4, len(pl.deck)))]
        pick = next((c for c in top
                     if "Red Panda" in c.classifications or c.is_song), None)
        if pick:
            top.remove(pick)
            pl.hand.append(pick)
            g.emit(f"RED PANDA POWER takes {pick.name}")
        for c in top:
            pl.deck.insert(0, c)
        return

    if name == "Merlin - Envisioning the Future":
        # MINOR TRICKERY: draw a card from the BOTTOM of your deck
        pl = g.players[p]
        if pl.deck:
            pl.hand.append(pl.deck.pop(0))
            g.emit("MINOR TRICKERY draws from the bottom")
        return

    if name == "Mickey Mouse - Detective":
        # GET A CLUE: top card of deck -> inkwell facedown & exerted
        pl = g.players[p]
        if pl.deck:
            pl.ink_cards.append(pl.deck.pop())
            pl.ink_total += 1   # exerted -> not ink_ready
            g.emit("GET A CLUE inks the top card")
        return

    if name == "Milo Thatch - Getting His Hands Dirty":
        # SCHOLAR'S GAMBIT: discard a card -> return chosen char to hand
        pl = g.players[p]
        tgt = _best_opp_char(g, p)
        if pl.hand and tgt:
            d = _worst_hand_card(g, p)
            pl.hand.remove(d)
            pl.discard.append(d)
            owner = tgt.owner
            del g.chars[tgt.uid]
            g.players[owner].hand.append(tgt.card)
            g.players[owner].discard.extend(tgt.under)
            g.players[owner].discard.extend(tgt.boosted)
            g.emit(f"SCHOLAR'S GAMBIT returns {tgt.card.base_name} to hand")
        return

    if name == "Scrooge McDuck - Reformed Ebenezer":
        # SPREADING JOY: put a card under each OTHER character of yours; if you
        # do, those characters gain Ward until the start of your next turn.
        for c in g.my_chars(p):
            if obj is not None and c.uid == obj.uid:
                continue
            if put_under(g, c, 1):
                g.effects.append({"kind": "ward", "target": c.uid,
                                  "amount": 0, "until": p})
        g.emit("SPREADING JOY grants Ward")
        return

    if name == "Tigger - Bouncing All the Way":
        tgt = _best_opp_char(g, p, cond=lambda g, c: c.card.cost <= 3)
        if tgt:
            owner = tgt.owner
            del g.chars[tgt.uid]
            g.players[owner].hand.append(tgt.card)
            g.emit(f"bounces {tgt.card.base_name} to hand")
        return

    # ---- Songs / actions ----
    if name == "Strength of a Raging Fire":
        tgt = _best_opp_char(g, p)
        if tgt:
            g.deal_damage(tgt, len(g.my_chars(p)))
        return

    if name == "He Hurled His Thunderbolt":
        tgt = _best_opp_char(g, p)
        if tgt:
            g.deal_damage(tgt, 4)
        for c in g.my_chars(p):
            if "Deity" in c.card.classifications:
                g.effects.append({"kind": "challenger", "target": c.uid,
                                  "amount": 2, "until": "eot"})
        return

    if name == "Grab Your Bow":
        for _ in range(2):
            tgt = _best_opp_char(g, p, cond=lambda g, c: g.eff_strength(c) <= 2)
            if not tgt:
                break
            g.banish_char(tgt)
        return

    if name == "Red Moon Ritual":
        tgt = _best_opp_char(g, p)
        if tgt:
            g.banish_char(tgt)
        return

    if name == "The Mob Song":
        for _ in range(3):
            tgt = _best_opp_char(g, p)
            if not tgt:
                break
            g.deal_damage(tgt, 3)
        return

    if name == "A Pirate's Life":
        amt = min(2, g.players[opp].lore)
        if amt:
            g.players[opp].lore -= amt
            g.emit("A PIRATE'S LIFE: opponent loses 2 lore")
        g.gain_lore(p, 2, "A Pirate's Life")
        return

    if name == "Like A Bird In the Sky":
        mine = [c for c in g.my_chars(p)]
        if mine:
            tgt = max(mine, key=lambda c: g.eff_lore(c))
            g.effects.append({"kind": "lore", "target": tgt.uid,
                              "amount": 1, "until": p})
            g.effects.append({"kind": "evasive", "target": tgt.uid,
                              "amount": 0, "until": p})
            g.emit(f"LIKE A BIRD: {tgt.card.base_name} +1 lore & Evasive")
        return

    if name == "We'll Save Our Village":
        for c in g.my_chars(p):
            g.effects.append({"kind": "resist", "target": c.uid,
                              "amount": 1, "until": p})
        for l in g.my_locs(p):
            g.effects.append({"kind": "resist", "target": l.uid,
                              "amount": 1, "until": p})
        g.emit("WE'LL SAVE OUR VILLAGE: Resist +1")
        return

    if name == "Marching Off to Battle":
        if ("banished_this_turn",) in g.turn_flags:
            g.draw(p, 2)
        return

    if name == "Akood et Emuti":
        g.discounts.append({"owner": p, "amount": 2, "filt": "character"})
        g.draw(p, 1)
        return

    if name == "Be King Undisputed":
        # Each opponent chooses and banishes one of their characters.
        pool = g.my_chars(opp)
        if pool:
            victim = min(pool, key=lambda c: (g.eff_lore(c), g.eff_strength(c)))
            g.emit(f"BE KING UNDISPUTED: P{opp} banishes {victim.card.base_name}")
            g.banish_char(victim)
        return

    if name == "Put That Thing Back":
        tgt = _best_opp_char(g, p)
        if tgt:
            owner = tgt.owner
            del g.chars[tgt.uid]
            g.players[owner].hand.append(tgt.card)
            g.players[owner].discard.extend(tgt.under)
            g.players[owner].discard.extend(tgt.boosted)
            g.emit(f"PUT THAT THING BACK returns {tgt.card.base_name}")
        return

    if name == "Mother Knows Best":
        tgt = _best_opp_char(g, p)
        if tgt:
            owner = tgt.owner
            del g.chars[tgt.uid]
            g.players[owner].hand.append(tgt.card)
            g.emit(f"MOTHER KNOWS BEST returns {tgt.card.base_name}")
        return

    if name == "Look at This Family":
        pl = g.players[p]
        top = [pl.deck.pop() for _ in range(min(5, len(pl.deck)))]
        chars = sorted([c for c in top if c.is_character], key=lambda c: -c.cost)[:2]
        for c in chars:
            top.remove(c)
            pl.hand.append(c)
        if chars:
            g.emit(f"LOOK AT THIS FAMILY takes {[c.name for c in chars]}")
        for c in top:
            pl.deck.insert(0, c)
        return

    if name == "Develop Your Brain":
        pl = g.players[p]
        top = [pl.deck.pop() for _ in range(min(2, len(pl.deck)))]
        if top:
            pick = max(top, key=lambda c: c.cost)
            top.remove(pick)
            pl.hand.append(pick)
            for c in top:
                pl.deck.insert(0, c)
            g.emit(f"DEVELOP YOUR BRAIN takes {pick.name}")
        return

    if name == "Education or Elimination":
        # Mode 2 (banish chosen damaged character) if available, else mode 1.
        tgt = _best_opp_char(g, p, cond=lambda g, c: c.damage > 0)
        if tgt:
            g.emit(f"EDUCATION OR ELIMINATION banishes {tgt.card.base_name}")
            g.banish_char(tgt)
            return
        g.draw(p, 1)
        mine = [c for c in g.my_chars(p)]
        if mine:
            m = max(mine, key=lambda c: g.eff_lore(c))
            g.effects.append({"kind": "lore", "target": m.uid, "amount": 1, "until": p})
            g.effects.append({"kind": "evasive", "target": m.uid, "amount": 0, "until": p})
        return

    # ===== AMBER/RUBY BOOST DECK =====
    if name == "Maleficent - Monstrous Dragon":
        # DRAGON FIRE: banish chosen character
        tgt = _best_opp_char(g, p)
        if tgt:
            g.emit(f"DRAGON FIRE banishes {tgt.card.base_name}")
            g.banish_char(tgt)
        return

    if name == "Gaston - Superior Archer":
        # WATCH THIS!: banish chosen character with 5 strength or more
        tgt = _best_opp_char(g, p, cond=lambda g, c: g.eff_strength(c) >= 5)
        if tgt:
            g.emit(f"WATCH THIS! banishes {tgt.card.base_name}")
            g.banish_char(tgt)
        return

    if name == "Red Alert":
        # Banish chosen character with 3 strength or less. If you have a
        # Monster character in play, chosen opponent loses 1 lore.
        tgt = _best_opp_char(g, p, cond=lambda g, c: g.eff_strength(c) <= 3)
        if tgt:
            g.emit(f"RED ALERT banishes {tgt.card.base_name}")
            g.banish_char(tgt)
        if any("Monster" in c.card.classifications for c in g.my_chars(p)):
            amt = min(1, g.players[opp].lore)
            if amt:
                g.players[opp].lore -= amt
                g.emit("RED ALERT: opponent loses 1 lore")
        return

    if name == "Raging Storm":
        for c in list(g.my_chars(p)) + list(g.my_chars(opp)):
            g.banish_char(c)
        g.emit("RAGING STORM banishes all characters")
        return

    if name == "The Horseman Strikes!":
        g.draw(p, 1)
        tgt = _best_opp_char(g, p, cond=lambda g, c: has_evasive(g, c))
        if tgt:
            g.emit(f"THE HORSEMAN STRIKES! banishes {tgt.card.base_name}")
            g.banish_char(tgt)
        return

    if name == "Della's Moon Lullaby":
        tgt = _debuff_target(g, p)
        if tgt:
            g.effects.append({"kind": "str", "target": tgt.uid,
                              "amount": -2, "until": p})
            g.emit(f"DELLA'S MOON LULLABY: {tgt.card.base_name} -2 str")
        g.draw(p, 1)
        return

    if name == "Besties, Assemble!":
        pl = g.players[p]
        top = [pl.deck.pop() for _ in range(min(4, len(pl.deck)))]
        chars = [c for c in top if c.is_character]
        if chars:
            pick = max(chars, key=lambda c: c.cost)
            top.remove(pick)
            pl.hand.append(pick)
            g.emit(f"BESTIES, ASSEMBLE! takes {pick.name}")
        for c in top:
            pl.deck.insert(0, c)
        return

    if name == "Sulley - The New Boss":
        # REHIRE: return a character card from discard to hand
        pool = [c for c in g.players[p].discard if c.is_character]
        if pool:
            tgt = max(pool, key=lambda c: c.cost)
            g.players[p].discard.remove(tgt)
            g.players[p].hand.append(tgt)
            g.emit(f"REHIRE returns {tgt.name} to hand")
        return

    # (Aurora is quest-triggered only; Elsa - Concerned Sister's THIS WAY
    # migrated to abilities_manual.json -- schema.dispatch_play handles it.)

    if name == "Lenny - Toy Binoculars":
        acts = [c for c in g.players[opp].hand if c.is_action]
        if acts:
            tgt = max(acts, key=lambda c: c.cost)
            g.players[opp].hand.remove(tgt)
            g.players[opp].discard.append(tgt)
            g.emit(f"Lenny discards {tgt.name} from P{opp}'s hand")

    elif name == "Elsa - Ice Artisan":
        _endless_winter(g, p)

    elif name == "The Queen - Devious Disguise":
        if params.get("scheme"):
            g.draw(p, 1)
            g.gain_lore(opp, 2, "EVIL SCHEME")

    elif name == "Woody - Helping a Friend":
        both = any(c.card.is_toy and c.uid != (obj.uid if obj else None)
                   for c in g.my_chars(p))
        ret = params.get("ret")
        free = params.get("free")
        if not both:
            # only one option may be used; params generator enforces this
            pass
        if ret:
            for c in g.players[p].discard:
                if c.name == ret and c.is_character and c.cost <= 2:
                    g.players[p].discard.remove(c)
                    g.players[p].hand.append(c)
                    g.emit(f"HANG ON! returns {c.name} to hand")
                    break
        if free:
            for c in list(g.players[p].hand):
                if c.name == free and c.is_character and c.cost <= 2:
                    g._play_card(p, c, {}, free=True)
                    break

    elif name == "Woody & Buzz Lightyear - Best Buddies":
        diff = len(g.players[opp].hand) - len(g.players[p].hand)
        if diff > 0:
            g.draw(p, diff)

    elif name == "You've Got a Friend in Me":
        pl = g.players[p]
        top = [pl.deck.pop() for _ in range(min(4, len(pl.deck)))]
        toys = sorted([c for c in top if c.is_character and c.is_toy],
                      key=lambda c: -c.cost)[:2]
        for c in toys:
            top.remove(c)
            pl.hand.append(c)
        g.emit(f"YGaFiM takes {[c.name for c in toys]}")
        for c in top:
            pl.deck.insert(0, c)

    elif name == "Under the Sea":
        for ch in list(g.my_chars(opp)):
            if g.eff_strength(ch) <= 2:
                del g.chars[ch.uid]
                g.players[opp].deck.insert(0, ch.card)
                for u in ch.under:
                    g.players[opp].deck.insert(0, u)
                g.emit(f"Under the Sea bottoms {ch.card.name}")

    elif name == "Get to Safety!":
        loc_name = params.get("loc")
        if loc_name:
            for c in g.players[p].discard:
                if c.name == loc_name and c.is_location and c.cost <= 3:
                    g.players[p].discard.remove(c)
                    from .engine import LocInPlay
                    l = LocInPlay(g.next_uid(), c, p)
                    g.locs[l.uid] = l
                    g.emit(f"Get to Safety! plays {c.name} from discard")
                    g.turn_flags.add(("played_loc", p))
                    on_play_location_triggers(g, p, l)
                    break
        if any(l.card.base_name == "Sleepy Hollow" for l in g.my_locs(p)):
            g.draw(p, 1)

    elif name == "Winterspell":
        lid = params.get("loc_id")
        if lid in g.locs:
            g.effects.append({"kind": "no_challenge", "target": lid,
                              "amount": 0, "until": p})
            g.emit(f"Winterspell protects {g.locs[lid].card.base_name}")
        g.draw(p, 1)

    elif name == "Touch the Sky":
        cid, lid = params.get("char"), params.get("loc")
        if cid in g.chars and lid in g.locs:
            ch, loc = g.chars[cid], g.locs[lid]
            ch.location = loc.uid
            g.emit(f"Touch the Sky moves {ch.card.base_name} to {loc.card.base_name}")
            on_move(g, ch, loc)
            g.draw(p, g.loc_lore(loc))

    elif name == "The Cold Never Bothered Me":
        pl = g.players[p]
        top = [pl.deck.pop() for _ in range(min(4, len(pl.deck)))]
        locs = sorted([c for c in top if c.is_location], key=lambda c: -c.lore)
        if locs:
            top.remove(locs[0])
            pl.hand.append(locs[0])
            g.emit(f"TCNBM takes {locs[0].name}")
        pl.discard.extend(top)
        g.discounts.append({"owner": p, "amount": 3, "filt": "location"})

    elif name == "Gantu - Hamsterviel's Accomplice":
        # EASY TARGET: choose and discard a card (mandatory once played)
        disc_name = params.get("discard")
        pl = g.players[p]
        target = None
        if disc_name:
            target = next((c for c in pl.hand if c.name == disc_name), None)
        if target is None and pl.hand:
            target = _worst_hand_card(g, p)
        if target:
            pl.hand.remove(target)
            pl.discard.append(target)
            g.emit(f"EASY TARGET discards {target.name}")

    # Phase 2: data-driven abilities
    schema.dispatch_play_type(g, p, card)
    if getattr(card, "is_song", False):
        # "Whenever an opponent plays a song" watchers (Signed Contract).
        schema.dispatch_opponent_song(g, 1 - p)
    if card.is_action:
        # Mark that an action is resolving so "whenever one of your actions
        # deals damage" watchers can attribute the damage (Merida STEADY AIM).
        prev = g.action_ctx
        g.action_ctx = (p, card)
        try:
            schema.dispatch_play(g, p, card, obj, params)
        finally:
            g.action_ctx = prev
    else:
        schema.dispatch_play(g, p, card, obj, params)

    # after-the-fact: playing a location fires location-play triggers
    if card.is_location:
        on_play_location_triggers(g, p, obj)
    # Lenny COMIN' UP FAST: once per turn (per copy), ready when you play an action
    if card.is_action:
        for c in g.my_chars(p):
            if c.card.name == "Lenny - Toy Binoculars" and c.exerted \
                    and ("lenny", c.uid) not in g.turn_flags:
                g.turn_flags.add(("lenny", c.uid))
                c.exerted = False
                g.emit("COMIN' UP FAST readies Lenny")


def consume_static(g, p, card):
    """Mark once-per-turn discounts as used after a paid play."""
    if card.is_character:
        for c in g.my_chars(p):
            if c.card.name == "Grandmother Willow - Ancient Advisor" \
                    and ("willow", c.uid) not in g.turn_flags:
                g.turn_flags.add(("willow", c.uid))


def on_play_location_triggers(g, p, loc_obj=None):
    """'Whenever you play a location' effects."""
    for c in g.my_chars(p):
        if c.card.name == "Elsa - Ice Artisan":
            _endless_winter(g, p)
    # Carl MOVING PARTNER: move Carl and up to 1 other of your characters to the
    # new location for free. Heuristic: bring Carl (to enable ADVENTURE AWAITS
    # draw) plus the readiest other character, when a fresh location exists.
    if loc_obj is not None:
        carls = [c for c in g.my_chars(p) if c.card.name == "Carl Fredricksen - On the Move"]
        for carl in carls:
            if carl.location != loc_obj.uid:
                carl.location = loc_obj.uid
                g.emit(f"MOVING PARTNER moves Carl to {loc_obj.card.base_name}")
                on_move(g, carl, loc_obj)
            others = [c for c in g.my_chars(p)
                      if c.uid != carl.uid and c.location != loc_obj.uid]
            if others:
                buddy = max(others, key=lambda c: (g.eff_lore(c), g.eff_strength(c)))
                buddy.location = loc_obj.uid
                g.emit(f"MOVING PARTNER moves {buddy.card.base_name} to {loc_obj.card.base_name}")
                on_move(g, buddy, loc_obj)
            break  # only one Carl trigger meaningfully applies per location


def _endless_winter(g, p):
    tgt = _best_opp_char(g, p, cond=lambda g, c: g.eff_strength(c) <= 3
                         and not c.exerted)
    if tgt:
        tgt.exerted = True
        g.emit(f"ENDLESS WINTER exerts {tgt.card.base_name}")


def _thorough_investigation(g, p):
    """Look at top 3; reveal a Detective character -> hand; rest to bottom."""
    pl = g.players[p]
    top = [pl.deck.pop() for _ in range(min(3, len(pl.deck)))]
    pick = next((c for c in top if c.is_character
                 and "Detective" in c.classifications), None)
    if pick:
        top.remove(pick)
        pl.hand.append(pick)
        g.emit(f"THOROUGH INVESTIGATION takes {pick.name}")
    for c in top:
        pl.deck.insert(0, c)


DRAWN_TOKEN = "__DRAWN__"


def clever_swap_options(g, p):
    """Discard choices for Rapunzel & Flynn's CLEVER SWAP quest variants:
    each distinct card name currently in hand, plus DRAWN_TOKEN meaning
    'discard whatever the quest-draw turns out to be' (the draw happens after
    the action is chosen, so it is unknown at selection time; ISMCTS values
    this option in expectation across determinizations)."""
    names = sorted({c.name for c in g.players[p].hand})
    return names + [DRAWN_TOKEN]


def on_quest(g, ch, sh_banish=False, choice=None):
    g_p = ch.owner
    name = ch.card.name

    # Mulan - Considerate Diplomat IMPERIAL INVITATION: on quest, look at top
    # 4; may reveal a Princess character -> hand; rest to bottom (any order).
    if name == "Mulan - Considerate Diplomat":
        deck = g.players[g_p].deck
        top4 = [deck.pop() for _ in range(min(4, len(deck)))]
        pick = next((c for c in top4
                     if c.is_character and "Princess" in c.classifications),
                    None)
        if pick is not None:
            top4.remove(pick)
            g.players[g_p].hand.append(pick)
            g.emit(f"IMPERIAL INVITATION takes {pick.name}")
        for c in top4:
            deck.insert(0, c)

    # Minnie Mouse - Sweetheart Princess BYE BYE, NOW: on quest, may banish
    # chosen exerted character with 5 or more Strength.
    if name == "Minnie Mouse - Sweetheart Princess":
        tgt = _best_opp_char(g, g_p,
                             cond=lambda g, c: c.exerted
                             and g.eff_strength(c) >= 5)
        if tgt is not None:
            g.emit(f"BYE BYE, NOW banishes {tgt.card.base_name}")
            g.banish_char(tgt, cause="ability")

    # Mike Wazowski FIND A FRIEND also fires whenever he quests.
    if name == "Mike Wazowski - Heroic Climber":
        _find_a_friend(g)

    # Rapunzel & Flynn Rider CLEVER SWAP: whenever this character quests, you
    # may draw a card, then choose and discard a card. Routed through
    # discard_card so discard counters/triggers (incl. FRESH START) fire.
    if name == "Rapunzel & Flynn Rider - Unlikely Pair":
        hand_before = len(g.players[g_p].hand)
        g.draw(g_p, 1)
        drew = len(g.players[g_p].hand) > hand_before
        drawn = g.players[g_p].hand[-1] if drew else None
        if g.players[g_p].hand:
            d = None
            if isinstance(choice, tuple) and len(choice) == 2 \
                    and choice[0] == "swap":
                want = choice[1]
                if want == DRAWN_TOKEN:
                    d = drawn
                else:
                    d = next((c for c in g.players[g_p].hand
                              if c.name == want), None)
            if d is None:
                d = _worst_hand_card(g, g_p)
            g.players[g_p].hand.remove(d)
            g.discard_card(g_p, d)
            g.emit(f"CLEVER SWAP: draws then discards {d.name}")
            g.count("R&F: CLEVER SWAP", g_p)

    if name == "Pocahontas & Meeko - Adventurous Friends":
        # WELCOME RETURN: you may return a cost-1 character of yours to hand;
        # if you do, you may play a cost-1 character for free. Heuristic:
        # bounce the weakest cost-1 already in play, then free-play the best
        # cost-1 available (may be the one just returned). Skip if none in play.
        p = g_p
        in_play_c1 = [c for c in g.my_chars(p)
                      if c.card.cost == 1 and c.uid != ch.uid]
        if in_play_c1:
            victim = min(in_play_c1,
                         key=lambda c: (g.eff_lore(c), g.eff_strength(c)))
            g.chars.pop(victim.uid, None)
            g.players[p].hand.append(victim.card)
            g.emit(f"WELCOME RETURN returns {victim.card.base_name} to hand")
            _free_play_cost1_from_hand(g, p, "WELCOME RETURN")

    if name == "Judy Hopps - Uncovering Clues":
        _thorough_investigation(g, g_p)
        return

    if name == "Cruella De Vil - Judgmental Traveler":
        # YOU'RE OUT OF FASHION: if you played another character this turn,
        # banish chosen damaged character.
        if ("played_char", g_p) in g.turn_flags:
            tgt = _best_opp_char(g, g_p, cond=lambda g, c: c.damage > 0)
            if tgt:
                g.emit(f"YOU'RE OUT OF FASHION banishes {tgt.card.base_name}")
                g.count("Cruella: OUT OF FASHION banish", g_p)
                g.banish_char(tgt)
        return

    if name == "Shere Khan - Fearsome Tiger":
        # ON THE HUNT: banish chosen opposing damaged character, then you may
        # put 1 damage counter on another chosen character.
        tgt = _best_opp_char(g, g_p, cond=lambda g, c: c.damage > 0)
        if tgt:
            g.emit(f"ON THE HUNT banishes {tgt.card.base_name}")
            g.banish_char(tgt)
        other = _best_opp_char(g, g_p)
        if other:
            other.damage += 1
            g.emit(f"ON THE HUNT: 1 damage on {other.card.base_name}")
            g.check_banish(other)
        return

    if name == "Max Goof - Chart Topper":
        # NUMBER ONE HIT: play a song of cost 4 or less from your discard for
        # free, then put it on the bottom of your deck.
        pl = g.players[g_p]
        pool = [c for c in pl.discard if c.is_song and c.cost <= 4]
        if pool:
            pick = max(pool, key=lambda c: c.cost)
            pl.discard.remove(pick)
            pl.hand.append(pick)          # _play_card pulls it from hand
            g.emit(f"NUMBER ONE HIT plays {pick.name} from the discard")
            g.count("MaxGoof: NUMBER ONE HIT", g_p)
            g._play_card(g_p, pick, {}, free=True, sung=True)
            # instead of going to the discard, it goes to the bottom of the deck
            if pick in pl.discard:
                pl.discard.remove(pick)
            pl.deck.insert(0, pick)
        return

    if name == "Stitch - Carefree Snowboarder":
        if len(g.my_chars(g_p)) - 1 >= 2:
            g.draw(g_p, 1)
        return

    if name == "Mushu - Stealthy Dragon":
        if len(g.players[1 - g_p].hand) > len(g.players[g_p].hand):
            g.draw(g_p, 1)
        return

    if name == "Minnie Mouse - Practical Traveler":
        if ("played_char", g_p) in g.turn_flags:
            g.gain_lore(g_p, 1, "DISCERNING EYE")
        return

    if name == "Mickey Mouse - Bob Cratchit":
        put_under(g, ch, 1)
        return

    if name == "Pete - Ghost of Christmas Future":
        n = len(ch.boosted)
        if n:
            pl = g.players[g_p]
            top = [pl.deck.pop() for _ in range(min(n, len(pl.deck)))]
            if top:
                pick = max(top, key=lambda c: c.cost)
                top.remove(pick)
                pl.hand.append(pick)
                for c in top:
                    pl.deck.insert(0, c)
                g.emit(f"FOREBODING GLANCE takes {pick.name}")
        return

    if name == "Eeyore - Hunny Scholar":
        # HUNNYTACTICS: chosen Hunny character of yours gets +1 lore and gains
        # Ward until the start of your next turn.
        mine = [c for c in g.my_chars(g_p) if is_hunny(g, c)]
        if mine:
            # prefer a ready, high-lore Hunny that isn't already warded
            tgt = max(mine, key=lambda c: (g.eff_lore(c), not has_ward(g, c)))
            g.effects.append({"kind": "lore", "target": tgt.uid,
                              "amount": 1, "until": g_p})
            g.effects.append({"kind": "ward", "target": tgt.uid,
                              "amount": 0, "until": g_p})
            g.emit(f"HUNNYTACTICS: {tgt.card.base_name} +1 lore & Ward")
        return

    if name == "Winnie the Pooh - Having a Think":
        # HUNNY POT: you may put a card from your hand into your inkwell
        pl = g.players[g_p]
        if pl.hand:
            c = _worst_hand_card(g, g_p)
            pl.hand.remove(c)
            pl.ink_cards.append(c)
            pl.ink_total += 1
            pl.ink_ready += 1
            g.emit(f"HUNNY POT inks {c.name}")
        return

    # (Aurora's BY YOUR LEAVE and Jessie's PART OF A FAMILY migrated to
    # abilities_manual.json -- handled by schema.dispatch_quest below.)

    if name == "Woody - Jungle Guide":
        g.draw(g_p, 1)
        _free_play(g, g_p, max_cost=2, chars_only=True)

    elif name == "Woody & Buzz Lightyear - Best Buddies":
        _free_play(g, g_p, max_cost=2, chars_only=False)

    elif name == "Carl Fredricksen - On the Move":
        # ADVENTURE AWAITS: quest while at a location -> draw = location's lore
        loc = g.locs.get(ch.location)
        if loc is not None:
            n = g.loc_lore(loc)
            if n:
                g.draw(g_p, n)

    elif name == "Pocahontas - Steadfast Traveler":
        # WANDERING SPIRIT: if you played another character this turn, return a
        # location from discard to hand. Heuristic: return the highest-lore location.
        played_others = any(
            f[0] == "played_char_uid" and f[1] != ch.uid
            for f in g.turn_flags)
        if played_others:
            locs = [c for c in g.players[g_p].discard if c.is_location]
            if locs:
                best = max(locs, key=lambda c: (c.lore, -c.cost))
                g.players[g_p].discard.remove(best)
                g.players[g_p].hand.append(best)
                g.emit(f"WANDERING SPIRIT returns {best.name} to hand")

    # Support (generic, Phase 1): when this character quests, add its Strength
    # to another chosen character's Strength this turn. Heuristic target: your
    # strongest OTHER character (they may challenge later this turn).
    if has_support(g, ch):
        others = [c for c in g.my_chars(g_p) if c.uid != ch.uid]
        if others:
            tgt = max(others, key=lambda c: (not c.exerted, g.eff_strength(c)))
            amt = g.eff_strength(ch)
            if amt > 0:
                g.effects.append({"kind": "str", "target": tgt.uid,
                                  "amount": amt, "until": "eot"})
                g.emit(f"Support: {tgt.card.base_name} +{amt} str this turn")
                # Rapunzel - Ready for Adventure ACT OF KINDNESS: whenever one
                # of your characters is chosen for Support, shield them: until
                # the start of your next turn, the next damage they'd take is
                # prevented. (Consumed in prevent_damage.)
                if any(c.card.name == "Rapunzel - Ready for Adventure"
                       for c in g.my_chars(g_p)):
                    g.effects.append({"kind": "aok_shield", "target": tgt.uid,
                                      "amount": 0, "until": g_p})
                    g.emit(f"ACT OF KINDNESS shields {tgt.card.base_name}")

    # Phase 2: data-driven abilities
    schema.dispatch_quest(g, ch)

    # Mickey SECRET PATH: your OTHER characters questing while he is exerted
    for m in g.my_chars(g_p):
        if m.card.name == "Mickey Mouse - Expedition Leader" and m.exerted \
                and m.uid != ch.uid:
            tgt = _debuff_target(g, g_p)
            if tgt:
                g.effects.append({"kind": "str", "target": tgt.uid,
                                  "amount": -2, "until": g_p})
                g.emit(f"SECRET PATH: {tgt.card.base_name} -2 str")

    # Sleepy Hollow: quest here MAY banish it for 2 lore + Evasive (AI's choice)
    loc = g.locs.get(ch.location)
    if sh_banish and loc and loc.card.name == "Sleepy Hollow - The Bridge":
        g.banish_loc(loc)
        g.gain_lore(g_p, 2, "HEAD FOR THE BRIDGE!")
        if g.winner is None:
            g.effects.append({"kind": "evasive", "target": ch.uid,
                              "amount": 0, "until": g_p})


def _free_play(g, p, max_cost, chars_only):
    """Heuristic: play the highest-cost qualifying card from hand for free,
    preferring characters."""
    hand = g.players[p].hand
    opts = [c for c in hand if c.cost <= max_cost and
            (c.is_character or (not chars_only and not c.is_song))]
    if not opts:
        return
    opts.sort(key=lambda c: (c.is_character, c.cost), reverse=True)
    g._play_card(p, opts[0], {}, free=True)


def on_banish(g, ch, cause="damage"):
    p = ch.owner
    name = ch.card.name
    g.turn_flags.add(("banished_this_turn",))
    g.turn_flags.add(("banished_name", name))
    g.turn_flags.add(("banished_base", ch.card.base_name))
    for _cls in ch.card.classifications:
        g.turn_flags.add(("banished_class", _cls))
    # data-driven "when this character is banished" triggers
    schema.dispatch_banish(g, ch, cause)
    schema.dispatch_leave_play(g, ch)
    schema.dispatch_ally_banished(g, ch)
    # Belle - Snowfield Strategist WINTER STOCKPILE: whenever one of your
    # characters is banished, you may put that card from your discard into your
    # inkwell facedown and exerted. Belle is deleted from play before this hook
    # runs, so also count the banished card itself when it is a Belle (the
    # ability self-triggers on her own banishment). Heuristic 'may': always
    # stockpile -- this card exists to turn banished bodies into ramp.
    if any(c.card.name == "Belle - Snowfield Strategist" for c in g.my_chars(p)) \
            or name == "Belle - Snowfield Strategist":
        if ch.card in g.players[p].discard:
            g.players[p].discard.remove(ch.card)
            g.players[p].ink_cards.append(ch.card)
            g.players[p].ink_total += 1   # facedown & exerted -> not ink_ready
            g.emit(f"WINTER STOCKPILE: {ch.card.name} -> inkwell")
            g.count("Belle WINTER STOCKPILE", p)
    if name == "Will o' the Wisp - Forest Spirit" and \
            ("wisp_return", ch.uid) in g.turn_flags:
        if ch.card in g.players[p].discard:
            g.players[p].discard.remove(ch.card)
            g.players[p].hand.append(ch.card)
            g.emit("COME ON OUT returns Will o' the Wisp to hand")
    if name == "Merlin - Envisioning the Future":
        # AGE OF INCONVENIENCE: put this card from discard on bottom of deck
        if ch.card in g.players[p].discard:
            g.players[p].discard.remove(ch.card)
            g.players[p].deck.insert(0, ch.card)
            g.emit("AGE OF INCONVENIENCE: Merlin returns to the bottom of the deck")
    if name == "Mickey Mouse - Bob Cratchit" and ch.boosted:
        # A GIVING HEART: move all cards under him under another character
        others = [c for c in g.my_chars(p) if c.uid != ch.uid]
        if others:
            tgt = max(others, key=lambda c: g.eff_lore(c))
            moved = [c for c in ch.boosted if c in g.players[p].discard]
            for c in moved:
                g.players[p].discard.remove(c)
                tgt.boosted.append(c)
            if moved:
                g.emit(f"A GIVING HEART moves {len(moved)} card(s) under "
                       f"{tgt.card.base_name}")
    if name == "Sulley & Boo - Scare Buddies":
        # THE POWER OF FRIENDSHIP: if any cards that were under them are
        # character cards, play those characters from discard for free.
        for c in list(ch.boosted):
            if c.is_character and c in g.players[p].discard:
                g.players[p].discard.remove(c)
                g.players[p].hand.append(c)
                g._play_card(p, c, {}, free=True)
                g.emit(f"THE POWER OF FRIENDSHIP free-plays {c.name}")
    if name == "Rex - Protective Dinosaur" and g.active != p:
        g.gain_lore(p, 1, "RUN AWAY!")
    if name == "Alien - True Believer" and g.active == p:
        # its own card just hit the discard (copies share Card objects, so
        # count Aliens: >=2 means 'another' exists beyond the banished one)
        pool = [c for c in g.players[p].discard if c.base_name == "Alien"
                and c.is_character]
        if len(pool) >= 2:
            g.players[p].discard.remove(pool[0])
            g.players[p].hand.append(pool[0])
            g.emit("HE HAS BEEN CHOSEN returns Alien to hand")


def on_move(g, ch, loc, zoo_draw=True):
    p = ch.owner
    from . import schema
    schema.dispatch_move(g, ch, loc)
    if loc.card.name == "Zootopia - Police Headquarters":
        flag = ("zoo", loc.uid)
        if zoo_draw and flag not in g.turn_flags and g.active == p and g.players[p].deck:
            g.turn_flags.add(flag)
            g.draw(p, 1)
            if g.players[p].hand:
                c = _worst_hand_card(g, p)
                g.players[p].hand.remove(c)
                g.players[p].discard.append(c)
                g.emit(f"NEW INFORMATION discards {c.name}")


def put_under(g, ch, n=1):
    """Put the top n cards of the owner's deck facedown under a character."""
    pl = g.players[ch.owner]
    put = 0
    for _ in range(n):
        if not pl.deck:
            break
        ch.boosted.append(pl.deck.pop())
        put += 1
    if put:
        g.emit(f"{ch.card.base_name}: {put} card(s) put under "
               f"({len(ch.boosted)} total)")
    return put


def replace_damage(g, ch, amount, challenge=False, source=None):
    """Damage replacement / prevention. Returns the modified amount."""
    if amount <= 0:
        return amount
    name = ch.card.name
    # Hercules EVER VIGILANT: can't be dealt damage unless being challenged.
    if name == "Hercules - Mighty Leader" and not challenge:
        return 0
    # EVER VALIANT: while an exerted Hercules is out, your other Hero
    # characters can't be dealt damage unless they're being challenged.
    if not challenge and "Hero" in ch.card.classifications:
        for h in g.my_chars(ch.owner):
            if h.card.name == "Hercules - Mighty Leader" and h.exerted \
                    and h.uid != ch.uid:
                return 0
    # Rapunzel - Ready for Adventure ACT OF KINDNESS shield: the next time
    # the supported character would be dealt damage, prevent it (one use).
    for e in list(g.effects):
        if e.get("kind") == "aok_shield" and e.get("target") == ch.uid:
            g.effects.remove(e)
            g.emit(f"ACT OF KINDNESS prevents damage to {ch.card.base_name}")
            return 0
    # Lilo EXTRA LAYERS: during each opponent's turn, the first time this
    # character would take damage, she takes no damage instead.
    if name == "Lilo - Bundled Up" and g.active != ch.owner:
        flag = ("extra_layers", ch.uid, g.turn)
        if flag not in g.turn_flags:
            g.turn_flags.add(flag)
            g.emit("EXTRA LAYERS prevents the damage")
            return 0
    return amount


def replace_banish(g, ch):
    """Banish replacement. Return True if the banish was replaced."""
    # Adventuring Duo THINKING OF YOU: inkwell facedown & exerted instead.
    if ch.card.name == "Mickey Mouse & Minnie Mouse - Adventuring Duo":
        p = ch.owner
        del g.chars[ch.uid]
        g.players[p].ink_cards.append(ch.card)
        g.players[p].ink_total += 1   # enters exerted -> not ink_ready
        g.players[p].discard.extend(ch.under)
        g.players[p].discard.extend(ch.boosted)
        g.emit("THINKING OF YOU: Adventuring Duo goes to the inkwell instead")
        return True
    return False


def kid_tastrophe(g, attacker, defender):
    """Boo KID-TASTROPHE!: whenever Boo challenges a character with 3 strength
    or less, banish that character (no damage dealt in that challenge)."""
    return (attacker.card.name == "Boo - Energetic Child"
            and g.eff_strength(defender) <= 3)


def defender_returns_when_challenged(g, attacker, defender):
    """Scrooge McDuck - S.H.U.S.H. Agent ON THE MOVE: when this character is
    challenged, return this card to its owner's hand (no damage dealt).
    Returns True if the defender was returned (challenge deals no damage)."""
    if defender.card.name != "Scrooge McDuck - S.H.U.S.H. Agent":
        return False
    if defender.uid in g.chars:
        del g.chars[defender.uid]
    # shift/boost cards under it go to discard, as on any leave-play
    g.players[defender.owner].discard.extend(getattr(defender, "under", []))
    g.players[defender.owner].discard.extend(getattr(defender, "boosted", []))
    g.players[defender.owner].hand.append(defender.card)
    g.emit("ON THE MOVE: Scrooge McDuck returns to hand")
    return True


def on_challenge(g, attacker, defender):
    """Called after a character-vs-character challenge resolves."""
    # "Whenever an opposing character challenges" watchers on the defending
    # side (Merida - Gifted Archer FIERCE PROTECTION).
    from . import schema
    schema.dispatch_opposing_challenge(g, attacker)
    schema.dispatch_challenges(g, attacker, defender)
    schema.dispatch_ally_challenges(g, attacker, defender)
    # location watchers on either side of the challenge
    if defender is not None:
        schema.dispatch_challenge_at_location(g, attacker, defender)
    # "Whenever this / one of your X characters is challenged" watchers.
    if defender is not None:
        schema.dispatch_challenged(g, defender, attacker)
    # Medallion Weights: whenever the buffed character challenges another
    # character this turn, you may draw a card.
    if any(e["kind"] == "medallion_draw" and e["target"] == attacker.uid
           for e in g.effects):
        g.draw(attacker.owner, 1)
        g.emit("DISCIPLINE AND STRENGTH draws a card")
    # Mr. Incredible LET'S DO THIS!: whenever one of your Super characters
    # challenges another character, draw a card.
    if "Super" in attacker.card.classifications:
        for m in g.my_chars(attacker.owner):
            if m.card.name == "Mr. Incredible - Super Strong":
                g.draw(attacker.owner, 1)
                g.emit("LET'S DO THIS! draws a card")
                break
    # Dr. Bushroot FAIR IS FAIR: whenever this character is challenged,
    # chosen opponent chooses and discards a card.
    if defender.card.name == "Dr. Bushroot - Evil Botanist":
        opp = attacker.owner
        if g.players[opp].hand:
            d = _worst_hand_card(g, opp)
            g.players[opp].hand.remove(d)
            g.players[opp].discard.append(d)
            g.emit(f"FAIR IS FAIR: P{opp} discards {d.name}")
    # Cursed Merfolk POOR SOULS / Joshua Sweet NO PATIENCE: whenever this
    # character is challenged, each opponent chooses and discards a card.
    if defender.card.name in ("Cursed Merfolk - Ursula's Handiwork",
                              "Joshua Sweet - Field Surgeon"):
        opp = attacker.owner
        if g.players[opp].hand:
            d = _worst_hand_card(g, opp)
            g.players[opp].hand.remove(d)
            g.players[opp].discard.append(d)
            lbl = ("POOR SOULS" if defender.card.name.startswith("Cursed")
                   else "NO PATIENCE")
            g.emit(f"{lbl}: P{opp} discards {d.name}")
    # Megara I'LL BE FINE: while there's a card under her, she gains
    # "whenever challenged, each opponent chooses and discards a card."
    if defender.card.name == "Megara - Secret Keeper" and defender.boosted:
        opp = attacker.owner
        if g.players[opp].hand:
            d = _worst_hand_card(g, opp)
            g.players[opp].hand.remove(d)
            g.players[opp].discard.append(d)
            g.emit(f"I'LL BE FINE: P{opp} discards {d.name}")
    # Tigger PROTECTIVE CHARGE: once during your turn, whenever this character
    # challenges another character, you may ready chosen Hunny character. If
    # you do, that character can't quest for the rest of this turn.
    if attacker.card.name == "Tigger - Hunny Barbarian" \
            and attacker.uid in g.chars \
            and ("charge", attacker.uid) not in g.turn_flags:
        p = attacker.owner
        mine = [c for c in g.my_chars(p)
                if is_hunny(g, c) and c.exerted and c.uid != attacker.uid]
        if mine:
            tgt = max(mine, key=lambda c: g.eff_strength(c))
            tgt.exerted = False
            g.turn_flags.add(("charge", attacker.uid))
            g.turn_flags.add(("no_quest", tgt.uid))
            g.emit(f"PROTECTIVE CHARGE readies {tgt.card.base_name}")


def _bottom_from_discard(g, chooser, target_player, why):
    """Put a card from target_player's discard on the bottom of their deck.
    The card leaving *target_player's* discard fires STROKE OF LUCK for
    target_player. Heuristic pick: the most expensive card (denies the most
    value / best rebuy). Returns True if a card was moved."""
    pile = g.players[target_player].discard
    if not pile:
        return False
    pick = max(pile, key=lambda c: c.cost)
    pile.remove(pick)
    g.players[target_player].deck.append(pick)
    g.emit(f"{why}: {pick.name} to bottom of P{target_player}'s deck")
    on_discard_leave(g, target_player, 1)
    return True


def on_discard(g, p, card):
    """Fired when a card goes from hand to discard."""
    # Rapunzel & Flynn Rider FRESH START: during your turn, whenever you
    # discard a character card (with R&F in play), you may play that character
    # from your discard, paying all costs. Recorded as a turn flag; exposed as
    # an action in activated_actions.
    if card.is_character and g.active == p \
            and any(c.card.name == "Rapunzel & Flynn Rider - Unlikely Pair"
                    for c in g.my_chars(p)):
        g.turn_flags.add(("fresh_start", p, card.name))
    # Look What You've Done: during your turn, when you discard this card, you
    # may play it from your discard (paying all costs). Exposed as a legal
    # action rather than auto-played -- see activated_actions.
    return


def on_discard_leave(g, p, count=1):
    """Fired when one or more cards LEAVE player p's discard pile (e.g. put on
    bottom of deck, played from discard). Kristoff - Icy Explorer STROKE OF
    LUCK: once during your turn, whenever a card leaves your discard, draw a
    card (the 'once' is enforced with a per-turn, per-Kristoff flag)."""
    for c in g.my_chars(p):
        if c.card.name == "Kristoff - Icy Explorer" \
                and ("stroke_of_luck", c.uid) not in g.turn_flags:
            g.turn_flags.add(("stroke_of_luck", c.uid))
            g.draw(p, 1)
            g.emit("STROKE OF LUCK: Kristoff draws a card")


def on_item_banished(g, item):
    """Fired when an item in play is banished."""
    # Darkwing TAKE THAT!: during your turn, whenever an item is banished,
    # you may pay 1 Ink to deal 2 damage to chosen character.
    p = g.active
    for c in g.my_chars(p):
        if c.card.name == "Darkwing Duck - Cool Under Pressure" \
                and g.players[p].ink_ready >= 1:
            tgt = _best_opp_char(g, p)
            if tgt:
                g.pay_ink(p, 1)
                g.emit(f"TAKE THAT! deals 2 to {tgt.card.base_name}")
                g.deal_damage(tgt, 2)
            break


def on_boost(g, p, obj):
    """Called after a card is put facedown under a character/location."""
    from . import schema
    schema.note_card_under(g, p, obj)
    schema.dispatch_card_under(g, p, obj, via_boost=True)
    # Cheshire Cat IT'S LOADS OF FUN: move up to 2 damage from chosen
    # character to chosen opposing character.
    if getattr(obj, "card", None) is not None and \
            obj.card.name == "Cheshire Cat - Inexplicable":
        mine = [c for c in g.my_chars(p) if c.damage > 0]
        tgt = _best_opp_char(g, p)
        if mine and tgt:
            src = max(mine, key=lambda c: c.damage)
            n = min(2, src.damage)
            src.damage -= n
            tgt.damage += n
            g.emit(f"IT'S LOADS OF FUN moves {n} damage to {tgt.card.base_name}")
            g.check_banish(tgt)
    # Webby's Diary LATEST ENTRY: whenever you put a card under one of your
    # characters or locations, you may pay 1 ink to draw a card. (Item is in
    # play as neither char nor loc in this engine -- items aren't modelled as
    # permanents, so we check the owner's play-history flag instead.)
    for _ in range(_count_diaries(g, p)):
        if g.players[p].ink_ready >= 1 and g.players[p].deck:
            g.pay_ink(p, 1)
            g.draw(p, 1)
            g.emit("LATEST ENTRY: paid 1, drew a card")


def _count_diaries(g, p):
    return sum(1 for i in g.items.get(p, []) if i.card.name == "Webby's Diary")


def _schema_activated_actions(g, p):
    """Generic, data-driven activated abilities from abilities_*.json.

    Emitted as ("activate", "schema", uid, index) so one card can expose more
    than one activated ability (e.g. Battering Ram's FULL FORCE / BREAK
    THROUGH). Cards whose activation is hand-written in apply_activated below
    are skipped by schema.entries_for(), so there is no double exposure.
    """
    acts = []
    objs = list(g.my_chars(p)) + list(g.items[p]) + list(g.my_locs(p))
    for obj in objs:
        ents = schema.activated_entries(obj.card.name)
        for i, e in enumerate(ents):
            if "effect" not in e:
                continue
            if schema.can_activate(g, p, obj, e):
                acts.append(("activate", "schema", obj.uid, i))
    return acts


def activated_actions(g, p):
    """Activated abilities exposed as engine actions."""
    acts = list(_schema_activated_actions(g, p))
    # Dumbo BREAKING RECORDS / MAKING HISTORY: [Exert], 1 Ink -- draw and
    # gain 1 lore. Dumbo grants the same to your other Evasive characters.
    has_dumbo = any(c.card.name == "Dumbo - Ninth Wonder of the Universe"
                    for c in g.my_chars(p))
    if g.players[p].ink_ready >= 1:
        for c in g.my_chars(p):
            if c.exerted or not g.is_dry(c):
                continue
            if c.card.name == "Dumbo - Ninth Wonder of the Universe" or \
                    (has_dumbo and has_evasive(g, c)):
                acts.append(("activate", "breaking_records", c.uid))
    # Angel GOOD AIM: once during your turn, discard a card to deal 2 damage
    for c in g.my_chars(p):
        if c.card.name == "Angel - Experiment 624" and g.players[p].hand \
                and ("good_aim", c.uid) not in g.turn_flags \
                and g.my_chars(1 - p):
            acts.append(("activate", "good_aim", c.uid))
            break
    # Rapunzel THE CALL OF ADVENTURE: once during your turn, you may discard a
    # card to give this character +1 Strength and Evasive until the start of
    # your next turn. No exert and no ink cost, so an undried (just-played)
    # Rapunzel may use it, and an exerted one may too.
    if g.active == p:
        for c in g.my_chars(p):
            if c.card.name == "Rapunzel - Escaping the Tower" \
                    and g.players[p].hand \
                    and ("call_of_adventure", c.uid) not in g.turn_flags:
                acts.append(("activate", "call_of_adventure", c.uid))
    # Hamm LOOSE CHANGE: [Exert] -- pay 1 less for the next character
    for c in g.my_chars(p):
        if c.card.name == "Hamm - Piggy Bank" and not c.exerted and g.is_dry(c):
            acts.append(("activate", "loose_change", c.uid))
            break
    # Broken Pod RENEWAL PROCESS: exert, 1 Ink -> put a card from chosen
    # player's discard on the bottom of their deck. Available if unexerted, ink
    # available, and some discard pile is non-empty.
    for it in g.items[p]:
        if it.card.name == "Broken Pod" and not it.exerted \
                and g.players[p].ink_ready >= 1 \
                and (g.players[0].discard or g.players[1].discard):
            acts.append(("activate", "renewal_process", it.uid))
            break
    # Magical Hunny Staff GIFT OF THE HIVE: once during your turn, pay 1 Ink
    # to give chosen character of yours the Hunny classification. (No exert.)
    for it in g.items[p]:
        if it.card.name == "Magical Hunny Staff" and g.players[p].ink_ready >= 1 \
                and ("gift_hive", it.uid) not in g.turn_flags \
                and any(not is_hunny(g, c) for c in g.my_chars(p)):
            acts.append(("activate", "gift_of_the_hive", it.uid))
            break
    # Rapunzel & Flynn Rider FRESH START: characters discarded this turn
    # (while R&F was in play) may be played from the discard, paying all costs.
    if g.active == p:
        # sorted(): turn_flags is a SET, so iterating it raw made action order
        # PYTHONHASHSEED-dependent (see the note on Woody above).
        for cname in sorted(f[2] for f in g.turn_flags
                            if isinstance(f, tuple) and len(f) == 3
                            and f[0] == "fresh_start" and f[1] == p):
            c = next((x for x in g.players[p].discard if x.name == cname), None)
            if c is not None and g.players[p].ink_ready >= c.cost:
                acts.append(("activate", "fresh_start_play", cname))
    # Look What You've Done: during your turn it may be played from the
    # discard (you pay all costs).
    if g.active == p:
        for c in g.players[p].discard:
            if c.name == "Look What You've Done" and \
                    g.players[p].ink_ready >= c.cost and g.my_chars(1 - p):
                acts.append(("activate", "lwyd_from_discard", 0))
                break
    # Item activations. Items ready in your ready step like characters; an
    # item played this turn is not yet dry and can't use an [Exert] ability.
    for it in g.items[p]:
        if it.exerted or not g.is_dry(it):
            continue
        n = it.card.name
        if n == "The Black Cauldron" and g.players[p].ink_ready >= 1:
            # THE CAULDRON CALLS or RISE AND JOIN ME! (one [Exert] per turn)
            if any(c.is_character for c in g.players[p].discard):
                acts.append(("activate", "cauldron_call", it.uid))
            if it.under and any(g.players[p].ink_ready - 1 >= c.cost
                                for c in it.under):
                acts.append(("activate", "cauldron_rise", it.uid))
        elif n == "Darkwing's Chair Set" and \
                any(c.damage > 0 for c in g.my_chars(p)):
            # SUDDEN SPIN: [Exert], banish -- heal 2 (4 if Darkwing chosen)
            acts.append(("activate", "sudden_spin", it.uid))
        elif n == "Ranger Plane":
            # BIG LIFT: [Exert] -- 10+ Strength character gets +3 lore
            if any(g.eff_strength(c) >= 10 for c in g.my_chars(p)):
                acts.append(("activate", "big_lift", it.uid))
        elif n == "The Thunderquack" and \
                ("challenge_banish_this_turn",) in g.turn_flags:
            # LAY OF THE LAND: [Exert] -- +1 lore if a challenge banish happened
            acts.append(("activate", "lay_of_the_land", it.uid))
        elif n == "Big Book Of Hunny" and g.players[p].ink_ready >= 2 \
                and g.players[p].deck:
            # INVOKE HUNNY: [Exert], 2 Ink -- reveal top; Hunny -> hand,
            # else bottom of deck.
            acts.append(("activate", "big_book", it.uid))
        elif n == "Magical Hunny Staff" and g.players[p].ink_ready >= 2 \
                and any(is_hunny(g, c) for c in g.my_chars(p)):
            # SPELL OF SWIFTNESS: [Exert], 2 Ink -- chosen Hunny of yours
            # gains Evasive until the start of your next turn.
            acts.append(("activate", "staff_swiftness", it.uid))
        elif n == "Junior Woodchuck Guidebook" and g.players[p].ink_ready >= 1 \
                and g.players[p].deck:
            # THE BOOK KNOWS EVERYTHING: [Exert], 1 Ink, Banish -- Draw 2.
            acts.append(("activate", "guidebook", it.uid))
        elif n == "Gyro-Evac" and g.players[p].ink_ready >= 1 and g.my_chars(p):
            # TAKE HER UP: [Exert], 1 Ink -- chosen character gains Evasive
            acts.append(("activate", "gyro_evasive", it.uid))
        elif n == "Gyro-Evac" and False:
            pass
        elif n == "Medallion Weights" and g.players[p].ink_ready >= 2:
            # DISCIPLINE AND STRENGTH: [Exert], 2 Ink -- chosen character gets
            # +2 Strength this turn; whenever they challenge another character
            # this turn, you may draw a card.
            mine = [c for c in g.my_chars(p) if not c.exerted and g.is_dry(c)]
            if mine:
                acts.append(("activate", "medallion", it.uid))
    # Aladdin ONLY THE BOLD: while there's a card under Aladdin, your Reckless
    # characters gain "[Exert] -- Gain 1 lore."
    if any(c.card.name == "Aladdin - Barreling Through" and c.boosted
           for c in g.my_chars(p)):
        for c in g.my_chars(p):
            if has_reckless(g, c) and not c.exerted and g.is_dry(c):
                acts.append(("activate", "only_the_bold", c.uid))
    return acts


def apply_activated(g, p, action):
    what, uid = action[1], action[2]
    if what == "schema":
        obj = g.chars.get(uid) \
            or next((x for x in g.items[p] if x.uid == uid), None) \
            or g.locs.get(uid)
        if obj is not None:
            schema.dispatch_activated(g, p, obj, action[3])
        return
    if what == "renewal_process":
        # Broken Pod: exert, 1 Ink -> put a card from chosen player's discard on
        # the bottom of their deck. Heuristic: target the opponent's discard
        # (deny their best rebuy); fall back to own if opponent's is empty.
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted or g.players[p].ink_ready < 1:
            return
        opp = 1 - p
        target = opp if g.players[opp].discard else p
        if not g.players[target].discard:
            return
        g.pay_ink(p, 1)
        it.exerted = True
        _bottom_from_discard(g, p, target, "RENEWAL PROCESS")
        return
    if what == "loose_change":
        ch = g.chars.get(uid)
        if ch and not ch.exerted:
            ch.exerted = True
            g.discounts.append({"owner": p, "amount": 1, "filt": "character"})
            g.emit("LOOSE CHANGE: next character costs 1 less")
        return
    if what == "cauldron_call":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted or g.players[p].ink_ready < 1:
            return
        pool = [c for c in g.players[p].discard if c.is_character]
        if not pool:
            return
        pick = max(pool, key=lambda c: c.cost)
        g.pay_ink(p, 1)
        it.exerted = True
        g.players[p].discard.remove(pick)
        it.under.append(pick)
        g.emit(f"THE CAULDRON CALLS: {pick.name} goes under the cauldron")
        return
    if what == "cauldron_rise":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted or g.players[p].ink_ready < 1:
            return
        g.pay_ink(p, 1)
        it.exerted = True
        playable = [c for c in it.under if g.players[p].ink_ready >= c.cost]
        if playable:
            pick = max(playable, key=lambda c: c.cost)
            it.under.remove(pick)
            g.players[p].hand.append(pick)
            g.pay_ink(p, pick.cost)
            g.emit(f"RISE AND JOIN ME! plays {pick.name}")
            g._play_card(p, pick, {}, free=True)
        return
    if what == "sudden_spin":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted:
            return
        mine = [c for c in g.my_chars(p) if c.damage > 0]
        if not mine:
            return
        tgt = max(mine, key=lambda c: (c.card.base_name == "Darkwing Duck",
                                       c.damage))
        heal = 4 if tgt.card.base_name == "Darkwing Duck" else 2
        tgt.damage = max(0, tgt.damage - heal)
        g.emit(f"SUDDEN SPIN heals {tgt.card.base_name}")
        g.banish_item(it)
        return
    if what == "big_lift":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted:
            return
        pool = [c for c in g.my_chars(p) if g.eff_strength(c) >= 10]
        if not pool:
            return
        tgt = max(pool, key=lambda c: g.eff_lore(c))
        it.exerted = True
        g.effects.append({"kind": "lore", "target": tgt.uid,
                          "amount": 3, "until": "eot"})
        g.emit(f"BIG LIFT: {tgt.card.base_name} +3 lore")
        return
    if what == "lay_of_the_land":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted:
            return
        it.exerted = True
        g.gain_lore(p, 1, "LAY OF THE LAND")
        return
    if what == "big_book":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted or g.players[p].ink_ready < 2:
            return
        pl = g.players[p]
        if not pl.deck:
            return
        g.pay_ink(p, 2)
        it.exerted = True
        top = pl.deck.pop()
        if "Hunny" in top.classifications:
            pl.hand.append(top)
            g.emit(f"INVOKE HUNNY reveals {top.name} -> hand")
        else:
            pl.deck.insert(0, top)
            g.emit(f"INVOKE HUNNY reveals {top.name} -> bottom")
        return
    if what == "gift_of_the_hive":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or g.players[p].ink_ready < 1:
            return
        pool = [c for c in g.my_chars(p) if not is_hunny(g, c)]
        if not pool:
            return
        tgt = max(pool, key=lambda c: g.eff_lore(c))
        g.pay_ink(p, 1)
        g.turn_flags.add(("gift_hive", it.uid))
        g.effects.append({"kind": "classification", "target": tgt.uid,
                          "what": "Hunny", "amount": 0, "until": p})
        g.emit(f"GIFT OF THE HIVE: {tgt.card.base_name} is a Hunny")
        return
    if what == "staff_swiftness":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted or g.players[p].ink_ready < 2:
            return
        pool = [c for c in g.my_chars(p) if is_hunny(g, c)]
        if not pool:
            return
        tgt = max(pool, key=lambda c: g.eff_lore(c))
        g.pay_ink(p, 2)
        it.exerted = True
        g.effects.append({"kind": "evasive", "target": tgt.uid,
                          "amount": 0, "until": p})
        g.emit(f"SPELL OF SWIFTNESS: {tgt.card.base_name} gains Evasive")
        return
    if what == "guidebook":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is not None:
            g.pay_ink(p, 1)
            g.banish_item(it)
            g.draw(p, 2)
            g.emit("THE BOOK KNOWS EVERYTHING: banished, drew 2")
        return
    if what == "medallion":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted:
            return
        # buff our best ready attacker
        mine = [c for c in g.my_chars(p) if not c.exerted and g.is_dry(c)]
        if not mine:
            return
        tgt = max(mine, key=lambda c: g.eff_strength(c))
        g.pay_ink(p, 2)
        it.exerted = True
        g.effects.append({"kind": "str", "target": tgt.uid,
                          "amount": 2, "until": "eot"})
        g.effects.append({"kind": "medallion_draw", "target": tgt.uid,
                          "amount": 1, "until": "eot"})
        g.emit(f"DISCIPLINE AND STRENGTH: {tgt.card.base_name} +2 str")
        return
    if what == "fresh_start_play":
        # uid slot carries the card name for this action
        cname = uid
        pl = g.players[p]
        card = next((c for c in pl.discard if c.name == cname), None)
        if card is None or pl.ink_ready < card.cost:
            return
        pl.discard.remove(card)
        pl.hand.append(card)              # _play_card pulls it from hand
        g.pay_ink(p, card.cost)
        g.turn_flags.discard(("fresh_start", p, cname))
        g.emit(f"FRESH START: {cname} is played from the discard")
        g.count("R&F: FRESH START replay", p)
        on_discard_leave(g, p, 1)
        g._play_card(p, card, {}, free=True)
        return
    if what == "lwyd_from_discard":
        pl = g.players[p]
        card = next((c for c in pl.discard if c.name == "Look What You've Done"), None)
        if card is None or pl.ink_ready < card.cost:
            return
        pl.discard.remove(card)
        pl.hand.append(card)              # _play_card pulls it from hand
        g.pay_ink(p, card.cost)
        g.emit("LOOK WHAT YOU'VE DONE is played from the discard")
        g._play_card(p, card, {}, free=True)
        return
    if what == "breaking_records":
        ch = g.chars.get(uid)
        if ch and not ch.exerted and g.players[p].ink_ready >= 1:
            g.pay_ink(p, 1)
            ch.exerted = True
            g.draw(p, 1)
            g.gain_lore(p, 1, f"BREAKING RECORDS ({ch.card.base_name})")
        return
    if what == "good_aim":
        ch = g.chars.get(uid)
        if ch and g.players[p].hand and ("good_aim", ch.uid) not in g.turn_flags:
            d = _worst_hand_card(g, p)
            g.players[p].hand.remove(d)
            g.players[p].discard.append(d)
            g.turn_flags.add(("good_aim", ch.uid))
            tgt = _best_opp_char(g, p)
            if tgt:
                g.emit(f"GOOD AIM discards {d.name}, deals 2")
                g.deal_damage(tgt, 2)
        return
    if what == "call_of_adventure":
        ch = g.chars.get(uid)
        if ch is None or not g.players[p].hand \
                or ("call_of_adventure", ch.uid) in g.turn_flags:
            return
        d = _worst_hand_card(g, p)
        g.players[p].hand.remove(d)
        g.discard_card(p, d)
        g.turn_flags.add(("call_of_adventure", ch.uid))
        # Both halves last until the start of your next turn, so they survive
        # the opponent's turn -- the point of the ability.
        g.effects.append({"kind": "str", "target": ch.uid,
                          "amount": 1, "until": p})
        g.effects.append({"kind": "evasive", "target": ch.uid,
                          "amount": 0, "until": p})
        g.emit(f"THE CALL OF ADVENTURE discards {d.name}: "
               f"{ch.card.base_name} +1 str & Evasive")
        return
    if what == "gyro_evasive":
        it = next((x for x in g.items[p] if x.uid == uid), None)
        if it is None or it.exerted or g.players[p].ink_ready < 1:
            return
        mine = g.my_chars(p)
        if not mine:
            return
        tgt = max(mine, key=lambda c: g.eff_lore(c))
        g.pay_ink(p, 1)
        it.exerted = True
        g.effects.append({"kind": "evasive", "target": tgt.uid,
                          "amount": 0, "until": p})
        g.emit(f"TAKE HER UP: {tgt.card.base_name} gains Evasive")
        return
    if what == "only_the_bold":
        ch = g.chars.get(uid)
        if ch and not ch.exerted:
            ch.exerted = True
            g.gain_lore(p, 1, f"ONLY THE BOLD ({ch.card.base_name})")


def on_sing(g, p, singer, song):
    """Called when `singer` sings `song` (single-singer Sing only)."""
    n = singer.card.name
    if n == "Meilin Lee - Popular Red Panda" and ("karaoke", singer.uid) not in g.turn_flags:
        g.turn_flags.add(("karaoke", singer.uid))
        g.gain_lore(p, 3, "KARAOKE QUEEN")
    elif n == "Powerline - World's Greatest Rock Star" \
            and ("mashup", singer.uid) not in g.turn_flags:
        g.turn_flags.add(("mashup", singer.uid))
        pl = g.players[p]
        top = [pl.deck.pop() for _ in range(min(4, len(pl.deck)))]
        pick = next((c for c in top if c.is_song and c.cost <= 9), None)
        for c in top:
            if c is not pick:
                pl.deck.insert(0, c)
        if pick:
            pl.hand.append(pick)
            g.emit(f"MASH-UP plays {pick.name} for free")
            g._play_card(p, pick, {}, free=True, sung=True)


def on_pay_to_play(g, p, card, paid):
    # Jessie YODEL-AY-HEE-HOO!: pay 2 ink or less to play a card
    if paid <= 2:
        for j in g.my_chars(p):
            if j.card.name == "Jessie - Lively Cowgirl":
                tgt = _debuff_target(g, p)
                if tgt:
                    g.effects.append({"kind": "str", "target": tgt.uid,
                                      "amount": -1, "until": p})
                    g.emit(f"YODEL: {tgt.card.base_name} -1 str")
    # Buzz Lightyear - On the Way. Fires for each of your Buzz in play when you
    # pay 2 or less to play a card. A Buzz just played this way does not trigger
    # on its own entry (the played card is what was paid for).
    if paid <= 2:
        buzzes = [c for c in g.my_chars(p)
                  if c.card.name == "Buzz Lightyear - On the Way"
                  and c.card is not card]
        for _ in buzzes:
            if card.is_character:
                # WORLD'S GREATEST TOY: deal 1 damage to a chosen opposing
                # damaged character. Prefer a target the ping actually kills
                # (damage+1 >= willpower); among those take the most valuable,
                # else fall back to the biggest damaged threat.
                tgt = _best_opp_char(
                    g, p,
                    cond=lambda g, c: c.damage > 0
                    and c.damage + 1 >= g.eff_willpower(c))
                if tgt is None:
                    tgt = _best_opp_char(g, p, cond=lambda g, c: c.damage > 0)
                if tgt is not None:
                    g.deal_damage(tgt, 1)
                    g.emit(f"WORLD'S GREATEST TOY: 1 damage to {tgt.card.base_name}")
            else:
                # SECRET MISSION: draw a card, then choose and discard a card.
                g.draw(p, 1)
                if g.players[p].hand:
                    d = _worst_hand_card(g, p)
                    g.players[p].hand.remove(d)
                    g.players[p].discard.append(d)
                    g.emit(f"SECRET MISSION: draw then discard {d.name}")


def start_of_turn(g, p):
    # The Queen - Conceited Ruler ROYAL SUMMONS: at start of your turn, may
    # discard a Princess or Queen character card to return a character card
    # from your discard to hand. Heuristic: only when it trades up in cost.
    for q in g.my_chars(p):
        if q.card.name != "The Queen - Conceited Ruler":
            continue
        fodder = [c for c in g.players[p].hand
                  if c.is_character and ("Princess" in c.classifications
                                         or "Queen" in c.classifications)]
        targets = [c for c in g.players[p].discard if c.is_character]
        if fodder and targets:
            pay = min(fodder, key=lambda c: c.cost)
            back = max(targets, key=lambda c: c.cost)
            if back.cost > pay.cost:
                g.players[p].hand.remove(pay)
                g.discard_card(p, pay)
                g.players[p].discard.remove(back)
                g.players[p].hand.append(back)
                on_discard_leave(g, p, 1)
                g.emit(f"ROYAL SUMMONS: discards {pay.name}, returns {back.name}")
        break
    for ch in list(g.my_chars(p)):
        if ch.card.name == "Mrs. Incredible - Super Stretchy":
            # FLEXIBLE THINKING: choose Evasive until your next turn, or +1
            # Lore this turn. Heuristic: take Evasive if the opponent has a
            # non-Evasive board that could otherwise challenge her.
            opp_can_hit = any(not has_evasive(g, c) for c in g.my_chars(1 - p))
            if opp_can_hit:
                g.effects.append({"kind": "evasive", "target": ch.uid,
                                  "amount": 0, "until": p})
                g.emit("FLEXIBLE THINKING: Mrs. Incredible gains Evasive")
            else:
                g.effects.append({"kind": "lore", "target": ch.uid,
                                  "amount": 1, "until": "eot"})
                g.emit("FLEXIBLE THINKING: Mrs. Incredible +1 lore")
        if ch.card.name == "Jack-Jack Parr - Incredible Potential":
            pl = g.players[p]
            if not pl.deck:
                continue
            c = pl.deck.pop()
            pl.discard.append(c)
            g.emit(f"Jack-Jack mills {c.name}")
            if c.is_character:
                g.effects.append({"kind": "str", "target": ch.uid,
                                  "amount": 2, "until": "eot"})
            elif c.is_action or c.card_type == "Item":
                g.effects.append({"kind": "lore", "target": ch.uid,
                                  "amount": 2, "until": "eot"})
            elif c.is_location:
                tgt = _best_opp_char(g, p)
                if tgt:
                    g.emit(f"Jack-Jack banishes {tgt.card.base_name}")
                    g.banish_char(tgt)


def end_of_turn(g, p):
    # Cinderella - Dream Come True WHATEVER YOU WISH FOR: at end of your turn,
    # if you played a Princess character this turn, you may put a card from
    # hand into your inkwell facedown to draw a card. Heuristic: always, using
    # the worst hand card, if hand is non-empty. Fires per Cinderella.
    if ("played_princess", p) in g.turn_flags:
        for c in g.my_chars(p):
            if c.card.name == "Cinderella - Dream Come True" \
                    and g.players[p].hand:
                ink = _worst_hand_card(g, p)
                g.players[p].hand.remove(ink)
                g.players[p].ink_cards.append(ink)
                g.players[p].ink_total += 1
                g.draw(p, 1)
                g.emit(f"WHATEVER YOU WISH FOR: inks {ink.name}, draws")

    # Lyle Tiberius Rourke DIRTY TRICKS: at end of your turn, if 2 or more cards
    # were put into your discard this turn, each opponent loses 1 lore.
    if g.turn_discards.get(p, 0) >= 2:
        if any(c.card.name == "Lyle Tiberius Rourke - Adventurer for Hire"
               for c in g.my_chars(p)):
            amt = min(1, g.players[1 - p].lore)
            if amt:
                g.players[1 - p].lore -= amt
                g.emit("DIRTY TRICKS: opponent loses 1 lore")
                g.count("Lyle: DIRTY TRICKS", p)
    # Meeko - Skittish Scrounger BOTTOMLESS PIT: at end of your turn, if he is
    # exerted, choose and discard a card OR banish him. Heuristic: pitch the
    # worst hand card if one is available; otherwise banish Meeko.
    for ch in list(g.my_chars(p)):
        if ch.card.name == "Meeko - Skittish Scrounger" and ch.exerted:
            if g.players[p].hand:
                worst = _worst_hand_card(g, p)
                g.players[p].hand.remove(worst)
                g.players[p].discard.append(worst)
                g.emit(f"BOTTOMLESS PIT: discards {worst.name}")
            else:
                g.emit("BOTTOMLESS PIT: no card to discard, banishes Meeko")
                g.banish_char(ch, cause="ability")

    # Temporary Shift: revert to the previous form at end of turn
    for e in [e for e in g.effects if e["kind"] == "temp_shift"]:
        ch = g.chars.get(e["target"])
        if ch is not None and ch.under:
            old = ch.under.pop()
            g.players[ch.owner].hand.append(ch.card)
            ch.card = old
            g.emit(f"Temporary Shift reverts to {old.base_name}")
    # Milo PRACTICAL KNOWLEDGE: if 2+ cards were put into your discard this
    # turn, draw a card.
    for ch in g.my_chars(p):
        if ch.card.name == "Milo Thatch - Getting His Hands Dirty":
            if g.turn_discards.get(p, 0) >= 2:
                g.draw(p, 1)
                g.emit("PRACTICAL KNOWLEDGE draws a card")
            break
    for ch in g.my_chars(p):
        if ch.card.name == "Elinor - Renowned Diplomat":
            exerted = sum(1 for c in g.my_chars(p) if c.exerted)
            if exerted >= 3:
                # damage target: prefer a character this kills, else best lore
                tgt = _best_opp_char(
                    g, p, key=lambda c: (g.eff_willpower(c) - c.damage -
                                         g.eff_resist(c) <= 1,
                                         g.eff_lore(c)))
                if tgt:
                    g.deal_damage(tgt, 1)
                g.gain_lore(p, 1, "COORDINATED EFFORTS")
                if g.winner is not None:
                    return
                g.draw(p, 1)


def on_challenge_banish(g, attacker, defender, atk_dies, def_dies):
    """Reactions to a challenge's banish outcome (fires on trades too)."""
    from . import schema
    if def_dies and defender is not None:
        schema.dispatch_challenged_banished(g, defender, attacker)
        # "Whenever this character banishes another character in a challenge"
        # (Raya - Headstrong), from the attacker's side.
        schema.dispatch_banishes_in_challenge(g, attacker, defender)
    # Goofy EVEN THE SCORE: whenever one of your OTHER Emerald characters is
    # challenged and banished, banish the challenging character.
    if def_dies and _is_emerald(defender.card):
        for gf in g.my_chars(defender.owner):
            if gf.card.name == "Goofy - Emerald Champion" and gf.uid != defender.uid:
                if attacker.uid in g.chars and not atk_dies:
                    g.emit(f"EVEN THE SCORE banishes {attacker.card.base_name}")
                    g.banish_char(attacker)
                break
    g.turn_flags.add(("challenge_banish_this_turn",))
    # Will o' the Wisp COME ON OUT: banished in a challenge -> return to hand
    for side, died in ((defender, def_dies), (attacker, atk_dies)):
        if died and side.card.name == "Will o' the Wisp - Forest Spirit":
            g.turn_flags.add(("wisp_return", side.uid))
    if def_dies and g.active == attacker.owner:
        p = attacker.owner
        # DW&LP VICTORY POSE: this character banishes another -> gain 2 lore
        if attacker.card.name == "Darkwing Duck & Launchpad - St. Canard's Finest":
            g.gain_lore(p, 2, "VICTORY POSE")
        # Nick Wilde CASE CLOSED: your Detective banishes -> draw a card
        if has_classification(g, attacker, "Detective"):
            for c in g.my_chars(p):
                if c.card.name == "Nick Wilde - Persistent Investigator":
                    g.draw(p, 1)
                    g.emit("CASE CLOSED draws a card")
                    break
        # Pluto WINNER TAKE ALL: your OTHER Steel character banishes -> +2 lore
        if "Steel" in str(attacker.card.ink_type):
            for c in g.my_chars(p):
                if c.card.name == "Pluto - Steel Champion" and c.uid != attacker.uid:
                    g.gain_lore(p, 2, "WINNER TAKE ALL")
                    break
    # Mrs. Incredible REGROUP: during your turn, whenever ANOTHER character is
    # banished in a challenge, ready chosen Super character (can't quest).
    if (def_dies or atk_dies):
        p = g.active
        for c in g.my_chars(p):
            if c.card.name == "Mrs. Incredible - Determined Rescuer" \
                    and ("regroup", c.uid) not in g.turn_flags:
                pool = [x for x in g.my_chars(p)
                        if has_classification(g, x, "Super") and x.exerted
                        and x.uid not in (attacker.uid, defender.uid)]
                if pool:
                    tgt = max(pool, key=lambda x: g.eff_strength(x))
                    tgt.exerted = False
                    g.turn_flags.add(("regroup", c.uid))
                    g.turn_flags.add(("no_quest", tgt.uid))
                    g.emit(f"REGROUP readies {tgt.card.base_name}")
                break
    # Tinker Bell PUNY PIRATE!: during your turn, whenever this character
    # banishes another character in a challenge, deal 2 damage to chosen
    # opposing character.
    if def_dies and attacker.card.name == "Tinker Bell - Giant Fairy" \
            and g.active == attacker.owner:
        tgt = _best_opp_char(g, attacker.owner)
        if tgt:
            g.emit(f"PUNY PIRATE! deals 2 to {tgt.card.base_name}")
            g.deal_damage(tgt, 2)


def after_challenge_banish(g, banisher):
    """CHEAP SHOT: once per turn per Nomanisan, when a character here banishes
    another character in a challenge, deal 2 damage to chosen character."""
    loc = g.locs.get(banisher.location)
    if loc and loc.card.name == "The Island of Nomanisan - Syndrome's Headquarters":
        flag = ("cheapshot", loc.uid)
        if flag not in g.turn_flags:
            g.turn_flags.add(flag)
            p = banisher.owner
            tgt = _best_opp_char(
                g, p, key=lambda c: (g.eff_willpower(c) - c.damage -
                                     max(0, g.eff_resist(c)) <= 2,
                                     g.eff_lore(c)))
            if tgt:
                g.emit(f"CHEAP SHOT hits {tgt.card.base_name}")
                g.deal_damage(tgt, 2)


# =====================================================================
# Play-parameter enumeration (real decision points exposed to the AI)
# =====================================================================
def play_param_options(g, p, card):
    """Yield tuples of (key, value) pairs; each tuple is one distinct way to
    play the card. Empty tuple = no choices."""
    name = card.name
    opts = []

    if card.is_character and can_enter_exerted(card):
        opts.append((("exerted", False),))
        opts.append((("exerted", True),))
    elif name == "The Queen - Devious Disguise":
        opts.append((("scheme", False),))
        opts.append((("scheme", True),))
    elif name == "Woody - Helping a Friend":
        both = any(c.card.is_toy for c in g.my_chars(p))
        rets = {c.name for c in g.players[p].discard
                if c.is_character and c.cost <= 2}
        frees = {c.name for c in g.players[p].hand
                 if c.is_character and c.cost <= 2 and c.name != name}
        # sorted(): rets/frees are SETS, and iterating them raw made the action
        # order depend on PYTHONHASHSEED -- so the same seed produced different
        # games in different processes. Every other branch here already sorts.
        rets_l, frees_l = sorted(rets), sorted(frees)
        if both:
            combos = [(r, f) for r in (rets_l + [None])
                      for f in (frees_l + [None])]
        else:
            combos = ([(r, None) for r in rets_l]
                      + [(None, f) for f in frees_l] + [(None, None)])
        for r, f in combos:
            opts.append((("ret", r), ("free", f)))
    elif name == "Get to Safety!":
        locs = {c.name for c in g.players[p].discard
                if c.is_location and c.cost <= 3}
        if locs:
            for l in sorted(locs):
                opts.append((("loc", l),))
        else:
            opts.append(())
    elif name == "Winterspell":
        mylocs = g.my_locs(p)
        if mylocs:
            for l in mylocs:
                opts.append((("loc_id", l.uid),))
        else:
            opts.append(())  # still draws a card
    elif name == "Ursula - Deceiver":
        # YOU'LL NEVER EVEN MISS IT: chosen opponent reveals their hand and
        # discards a song of your choice. Expose each distinct song in the
        # opponent's hand; no-op option if they hold none.
        songs = {c.name for c in g.players[1 - p].hand if c.is_song}
        if songs:
            for n in sorted(songs):
                opts.append((("song", n),))
        else:
            opts.append(())
    elif name in ("Pocahontas - Guiding the Tribe",
                  "Pocahontas & Meeko - Adventurous Friends"):
        # STAY CLOSE: you MAY play a cost-1 character for free. Expose each
        # distinct cost-1 character in hand, plus the option to decline.
        c1 = {c.name for c in g.players[p].hand
              if c.is_character and c.cost == 1 and c.name != name}
        for n in sorted(c1):
            opts.append((("free1", n),))
        opts.append((("free1", None),))
    elif name == "Aladdin - Doing His Part":
        # CLEAR IT OUT: may pay 1 Ink to banish chosen item. Expose each item
        # on the board (either player's) plus declining.
        if g.players[p].ink_ready >= 1:
            for pl in (0, 1):
                for it in g.items[pl]:
                    opts.append((("clearit", it.uid),))
        opts.append((("clearit", None),))
    elif name == "Rapunzel - Tower Defender":
        # THE FATE'S DESIGN: may discard a card to return a chosen character to
        # hand. Expose (discard card name, return-target uid) pairs + decline.
        disc_names = {c.name for c in g.players[p].hand if c.name != name}
        rets = [c for c in g.chars.values() if not has_ward(g, c)]
        if disc_names and rets:
            for dn in sorted(disc_names):
                for r in rets:
                    opts.append((("fate_disc", dn), ("fate_ret", r.uid)))
        opts.append((("fate_disc", None), ("fate_ret", None)))
    elif name == "Gantu - Hamsterviel's Accomplice":
        # EASY TARGET: choose and discard a card. Expose each distinct hand card
        # (minus Gantu itself) as a discard choice; fall back to no-op if empty.
        others = {c.name for c in g.players[p].hand if c.name != name}
        if others:
            for n in sorted(others):
                opts.append((("discard", n),))
        else:
            opts.append(())
    elif name == "Touch the Sky":
        pairs = [(c.uid, l.uid) for c in g.my_chars(p) for l in g.my_locs(p)
                 if c.location != l.uid]
        if pairs:
            for cu, lu in pairs:
                opts.append((("char", cu), ("loc", lu)))
        # unplayable with no valid move; no fallback option
    else:
        opts.append(())

    # Shift variants (generic, Phase 1): any card with printed 'Shift N' may be
    # played onto one of your characters sharing its base name.
    if card.shift_ink is not None:
        base_name = card.base_name
        from . import schema
        # A card may declare which names it can be shifted ONTO (Tod & Copper
        # shifts onto a character named Tod or Copper).
        onto = set(schema.shift_onto_names(card))
        for c in g.my_chars(p):
            # ...and a card may declare extra names it counts AS for Shift
            # (Incrediboy SPOILER ALERT counts as Syndrome).
            if c.card.base_name == base_name \
                    or base_name in schema.shift_aliases(c.card) \
                    or c.card.base_name in onto:
                opts.append((("shift", c.uid),))
    # Combo Shift N: may shift onto a character named after either half of the
    # combo name (e.g. Sulley & Boo -> a 'Sulley' or a 'Boo').
    elif combo_shift_cost(card) is not None:
        targets = combo_shift_names(card)
        for c in g.my_chars(p):
            if c.card.base_name in targets:
                opts.append((("shift", c.uid),))
    # Duo Shift N: needs TWO characters, one named after each half.
    elif duo_shift_cost(card) is not None:
        names = combo_shift_names(card)
        if len(names) == 2:
            first = [c for c in g.my_chars(p) if c.card.base_name == names[0]]
            second = [c for c in g.my_chars(p) if c.card.base_name == names[1]]
            for a in first:
                for b in second:
                    if a.uid != b.uid:
                        opts.append((("shift", a.uid), ("duo_other", b.uid)))
    # Temporary Shift N: shift onto a same-named character; reverts at EOT.
    elif temporary_shift_cost(card) is not None:
        # "Temporary Red Panda Shift 2" shifts onto any character with that
        # classification; the plain form shifts onto a same-named character.
        want = temporary_shift_classification(card)
        for c in g.my_chars(p):
            ok = (want in c.card.classifications) if want \
                else (c.card.base_name == card.base_name)
            if ok:
                opts.append((("shift", c.uid), ("temporary", True)))
    return opts


# =====================================================================
# Coverage registry: card names whose named abilities are implemented in
# Python here (or in the engine). Used by the coverage report. Cards fully
# covered by keywords/vanilla, or by schema entries, need not be listed.
# =====================================================================
HAND_IMPLEMENTED = {
    # --- amber_sapphire_princess (control variant) ---
    "Hades - Infernal Schemer",                    # IS THERE A DOWNSIDE inkwell removal
    "Belle - Snowfield Strategist",                # WINTER STOCKPILE banish->ink ramp
    "Moana - Curious Explorer",                    # ANCESTRAL LEGACY ink from discard
    # --- amber_sapphire_princess ---
    "Anna - Braving the Storm",                    # I WAS BORN READY
    "The Queen - Conceited Ruler",                 # ROYAL SUMMONS
    "Mulan - Considerate Diplomat",                # IMPERIAL INVITATION
    "Mowgli - Man Cub",                            # HAVE A BETTER LOOK
    "Rapunzel - Ready for Adventure",              # ACT OF KINDNESS
    "World's Greatest Criminal Mind",              # banish 5+ str
    "Cinderella - Dream Come True",                # WHATEVER YOU WISH FOR
    "Aurora - Dreaming Guardian",                  # PROTECTIVE EMBRACE ward
    "Alice - Growing Girl",                        # GOOD ADVICE + WHAT DID I DO?
    "Minnie Mouse - Sweetheart Princess",          # ROYAL FAVOR + BYE BYE NOW
    # --- toys v2 / emerald_steel candidates ---
    "Mike Wazowski - Heroic Climber",              # FIND A FRIEND reveal
    "RC - Remote-Controlled Car",                  # LOW BATTERIES surcharge
    "Rapunzel & Flynn Rider - Unlikely Pair",      # CLEVER SWAP + FRESH START
    # --- amethyst_emerald_stun ---
    "Lyle Tiberius Rourke - Adventurer for Hire",  # EYE FOR VALUE + DIRTY TRICKS
    "Aladdin - Doing His Part",                    # CLEAR IT OUT item banish
    "Rapunzel - Tower Defender",                   # THE FATE'S DESIGN discard+bounce
    "Kristoff - Icy Explorer",                     # HIDDEN DEPTHS + STROKE OF LUCK
    "Chernabog - Unnatural Force",                 # DARK DANCE shuffle+replay
    "Broken Pod",                                  # RENEWAL PROCESS activated
    # --- damage_discard ---
    "Cursed Merfolk - Ursula's Handiwork",        # POOR SOULS on-challenged discard
    "Joshua Sweet - Field Surgeon",               # NO PATIENCE on-challenged discard
    "Scrooge McDuck - S.H.U.S.H. Agent",          # BACKUP PLAN + ON THE MOVE
    "Ursula - Deceiver",                          # discard a song on play
    "You Broke My Smolder",                       # discard hand, draw 2
    "Snowball Fight",                             # each opp discards + evasive lore
    "Nala - Undaunted Lioness",                   # +1 lore/Resist while undamaged
    "Snow Fort",                                  # team +1 str, Resist on defense
    "Megara - Secret Keeper",                     # I'LL BE FINE while boosted
    "Buzz Lightyear - On the Way",                # SECRET MISSION / WORLD'S GREATEST TOY
    # --- amber_amethyst (aggressive Pocahontas engine) ---
    "Merida - Wisp Conjurer",                     # FOCUSED ENERGY + BECKON
    "Pocahontas - Guiding the Tribe",             # STAY CLOSE free-play
    "Pocahontas & Meeko - Adventurous Friends",   # WELCOME RETURN bounce+free
    "Meeko - Skittish Scrounger",                 # BOTTOMLESS PIT eot drawback
    "Pocahontas - Peacekeeper",                   # CALMING WORDS challenge-lock
    # --- amber_amethyst ---
    "Dale - Ready for His Shot", "Hades - Looking for a Deal",
    "Hamm - Piggy Bank", "Ohana Means Family", "Rafiki - Mystical Fighter",
    "The Black Cauldron", "Will o' the Wisp - Forest Spirit",
    # --- detectives ---
    "Darkwing Duck & Launchpad - St. Canard's Finest",
    "Darkwing Duck - Cool Under Pressure", "Darkwing's Chair Set",
    "Judy Hopps - Lead Detective", "Judy Hopps - On the Case",
    "Judy Hopps - Uncovering Clues", "Mrs. Incredible - Determined Rescuer",
    "Nick Wilde - Persistent Investigator", "One Last Hope",
    "Pluto - Steel Champion", "Ranger Plane",
    "The Terror That Flaps in the Night", "The Thunderquack",
    # --- emerald_steel_ping ---
    "Bobby Zimuruski - Spray Cheese Kid", "Chomp!",
    "Cruella De Vil - Judgmental Traveler", "Dinky - Has the Brains",
    "Goofy - Emerald Champion", "Look What You've Done",
    "Malicious, Mean, and Scary", "Max Goof - Chart Topper",
    "Shere Khan - Fearsome Tiger", "Strike A Good Match",
    "Tinker Bell - Giant Fairy", "Windstorm",
    # --- tink_pan / lilo_stitch / mickie_minnie / ruby_steel_songs ---
    "A Pirate's Life", "Akood et Emuti", "Angel - Experiment 624",
    "Angel - Siren Singer", "Ariel - Adventurous Collector",
    "Ariel - Determined Mermaid", "Be King Undisputed",
    "Cheshire Cat - Inexplicable", "Chief Powhatan - Protective Leader",
    "Develop Your Brain", "Dr. Bushroot - Evil Botanist",
    "Dumbo - Ninth Wonder of the Universe", "Education or Elimination",
    "Grab Your Bow", "Gyro-Evac", "He Hurled His Thunderbolt",
    "Hercules - Mighty Leader", "Like A Bird In the Sky", "Lilo - Bundled Up",
    "Lilo - Snow Artist", "Look at This Family", "Marching Off to Battle",
    "Max Goof - Rockin' Teen", "Meilin Lee - Losing Control",
    "Meilin Lee - Popular Red Panda", "Merlin - Envisioning the Future",
    "Mickey Mouse & Minnie Mouse - Adventuring Duo",
    "Mickey Mouse - Bob Cratchit", "Mickey Mouse - Detective",
    "Milo Thatch - Getting His Hands Dirty",
    "Minnie Mouse - Practical Traveler", "Mother Knows Best",
    "Mr. Incredible - Super Strong", "Mrs. Incredible - Super Stretchy",
    "Mushu - Stealthy Dragon", "Pete - Ghost of Christmas Future",
    "Peter Pan & Tinker Bell - Fast Friends", "Peter Pan - Playful Prankster",
    "Powerline - World's Greatest Rock Star", "Put That Thing Back",
    "Red Moon Ritual", "Scrooge McDuck - Reformed Ebenezer",
    "Sisu - Daring Visitor", "Stitch - Carefree Snowboarder",
    "Strength of a Raging Fire", "The Mob Song", "Tigger - Bouncing All the Way",
    "Violet Parr - Learning New Powers", "We'll Save Our Village",
    # --- Hunny deck ---
    "Christopher Robin - Hunny Sage",
    "Winnie The Pooh - Hunny Archmage",
    "Winnie The Pooh & Piglet - Hunny Mages",
    "Eeyore - Hunny Scholar",
    "Owl - Hunny Ranger",
    "Gopher - Hunny Cook",
    "Roo - Hunny Rogue",
    "Rabbit - Hunny Paladin",
    "Tigger - Hunny Barbarian",
    "Winnie the Pooh - Having a Think",
    "Isis Vanderchill - Ice Queen of St. Canard",
    "Demona - Scourge of the Wyvern Clan",
    "Let It Go",
    "Junior Woodchuck Guidebook",
    "Distract", "Come Out and Fight!", "Performance Review",
    "Big Book Of Hunny", "Magical Hunny Staff",
    "Hundred Acre Wood - Hunny Campsite",
    # --- Amber/Ruby boost deck ---
    "Maleficent - Monstrous Dragon",
    "Gaston - Superior Archer",
    "Red Alert",
    "Raging Storm",
    "The Horseman Strikes!",
    "Della's Moon Lullaby",
    "Besties, Assemble!",
    "Sulley - The New Boss",
    "Sulley & Boo - Scare Buddies",
    "Scrooge McDuck - Ghostly Ebenezer",
    "Hercules - Spectral Demigod",
    "Aladdin - Barreling Through",
    "Boo - Energetic Child",
    "Liquidator - Iced Over",
    "Ariel - Ethereal Voice",
    "Webby's Diary",
    "Medallion Weights",
    "Scrooge's Counting House - Ebenezer's Office",
    # --- pre-existing ---
    "Lenny - Toy Binoculars",
    "Elsa - Ice Artisan",
    "The Queen - Devious Disguise",
    "Woody - Helping a Friend",
    "Woody & Buzz Lightyear - Best Buddies",
    "You've Got a Friend in Me",
    "Under the Sea",
    "Get to Safety!",
    "Winterspell",
    "Touch the Sky",
    "The Cold Never Bothered Me",
    "Gantu - Hamsterviel's Accomplice",
    "Jessie - Lively Cowgirl",            # YODEL (PART OF A FAMILY is schema)
    "Woody - Jungle Guide",
    "Carl Fredricksen - On the Move",
    "Pocahontas - Steadfast Traveler",
    "Mickey Mouse - Expedition Leader",
    "Grandmother Willow - Ancient Advisor",
    "Bullseye - Loyal Horse",
    "Alien - True Believer",
    "Rex - Protective Dinosaur",          # RUN AWAY! (Bodyguard is keyword)
    "Jack-Jack Parr - Incredible Potential",
    "Elinor - Renowned Diplomat",
    "Lumiere - Fiery Friend",
    "John Silver - Greedy Treasure Seeker",
    "Illuminary Tunnels - Linked Caverns",
    "Launchpad - Hideout Defender",
    "Castle Wyvern - Above the Clouds",
    "The Island of Nomanisan - Syndrome's Headquarters",
    "Sleepy Hollow - The Bridge",
    "Zootopia - Police Headquarters",
    "Beast - Snowfield Troublemaker",     # DYNAMIC MANEUVER (Rush is keyword)
    # --- emerald_steel_ping v3 ---
    "Rapunzel - Escaping the Tower",      # THE CALL OF ADVENTURE (activated)
}


# =====================================================================
# Manifest of implementations & assumptions, for user audit
# =====================================================================
ASSUMPTIONS = [
 "PHASE 1 KEYWORDS are generic pool-wide: printed Bodyguard, Evasive, Alert, Rush, Ward, "
 "Reckless, Support, Resist +N, Challenger +N, Singer N, Shift N, and Sing Together N are "
 "parsed from card text and enforced by the engine with no per-card code. Prose-granted "
 "keywords ('gains Evasive...') are deliberately NOT parsed as printed.",
 "Alert: lets a character challenge Evasive defenders, but does NOT make the character itself "
 "Evasive -- an Alert character is still challengeable normally.",
 "Ward: excluded from all opponent-'chosen' auto-targeting (debuffs, Elinor, CHEAP SHOT, "
 "ENDLESS WINTER, Jack-Jack). Mass effects (Under the Sea) still apply. Vanish is parsed as a "
 "keyword token but has no engine effect yet (no printed Vanish in the current decks).",
 "Reckless: cannot quest; ending the turn is illegal while a ready Reckless character has a "
 "legal challenge (the 'must challenge if able' enforcement).",
 "Support: on quest, adds the quester's Strength to your best other character until end of "
 "turn (heuristic target: prefers ready, then highest Strength; not an AI choice).",
 "Zootopia (NEW INFORMATION) and Sleepy Hollow (HEAD FOR THE BRIDGE!) are exposed as REAL "
 "AI CHOICES, not forced: moving to Zootopia offers 'zoo_draw' vs 'zoo_skip'; questing at "
 "Sleepy Hollow offers 'sh_banish' vs 'sh_keep'. The MCTS decides. (When these fire from "
 "auto-moves like Touch the Sky or Carl's MOVING PARTNER, the draw defaults to ON.)",
 "Zootopia's discard (once the AI opts to draw) prefers a location cost<=3 "
 "(recoverable with Get to Safety!), else the cheapest card in hand.",
 "'Chosen character' triggers are auto-targeted with heuristics, not AI decisions: "
 "strength debuffs (Jessie YODEL, Mickey SECRET PATH) hit the strongest opposing character; "
 "Elinor/CHEAP SHOT damage prefers a killing blow, else highest lore; "
 "ENDLESS WINTER exerts the highest-value ready opposing character with str<=3.",
 "Gantu (EASY TARGET) exposes its mandatory discard as a real AI choice (each distinct hand "
 "card is an option); if not chosen explicitly it discards the cheapest/most-recoverable card.",
 "Carl Fredricksen (MOVING PARTNER): on playing a location, Carl and the highest-value other "
 "character are moved there for free (heuristic; the 'up to 1 other' is auto-selected, not an AI choice).",
 "Carl (ADVENTURE AWAITS) draws = that location's current lore when he quests at a location.",
 "Pocahontas (WANDERING SPIRIT): if another character was played this turn, returns the "
 "highest-lore location from discard to hand (auto-selected).",
 "Launchpad (STAND GUARD) grants your locations Resist +1, applied when locations are challenged.",
 "Lenny discards the highest-cost action from the revealed hand.",
 "You've Got a Friend in Me takes the 2 highest-cost Toy characters revealed; "
 "The Cold Never Bothered Me takes the highest-lore location revealed.",
 "Jack-Jack's start-of-turn mill is always used; a milled location banishes the "
 "opponent's best character (highest lore).",
 "Sing Together singers are chosen greedily: lowest lore first, highest cost, until total >= song cost. "
 "Regular singing exerts the best available (highest-cost, lowest-lore) eligible singer.",
 "Free-play triggers (Woody Jungle Guide / Woody & Buzz quests) auto-play the highest-cost "
 "qualifying card from hand, preferring characters.",
 "Playing a card for free counts as paying 0 ink for Jessie's YODEL trigger.",
 "Characters may move to any of your locations by paying the move cost, any number of "
 "times per turn, but never to the location they're already at.",
 "Inkwell card identities are tracked but treated as public for simplicity "
 "(they never re-enter play in these decks, so the information leak is negligible).",
 "Rapunzel - Escaping the Tower (THE CALL OF ADVENTURE) is a real AI decision, exposed as an "
 "activated action. It costs no ink and no exert, so a just-played (undried) or already-exerted "
 "Rapunzel may still use it. The default policy fires it only with 4+ cards in hand and only "
 "while she lacks Evasive; MCTS may choose otherwise. Both halves (+1 Strength, Evasive) expire "
 "at the start of your next turn, so they cover the opponent's turn. The discarded card is "
 "_worst_hand_card and goes through discard_card, so Look What You've Done and FRESH START see it.",
 "Tod - Clever Fox (PROBLEM SOLVING) and Tinker Bell - Most Helpful (PIXIE DUST) are schema "
 "entries, not Python. PROBLEM SOLVING's discard is mandatory and picks the worst card in hand. "
 "PIXIE DUST's 'chosen character' resolves to your highest-Lore character (possibly Tink "
 "herself), matching the Gyro-Evac TAKE HER UP heuristic; it is not exposed as a choice.",
 "Mulligan (both AI and default): cards costing 5+ are bottomed and redrawn.",
 "Under the Sea bottom-decks in an arbitrary fixed order rather than a chosen order.",
 "No printed Evasive/Ward/Support/Singer exists in either deck; only the granted "
 "Evasive from Sleepy Hollow is modeled.",
]


# See the note in engine.py. `from .engine import LocInPlay` stays deferred
# (single cold call site) so importing abilities never pulls in engine.
from . import schema, keywords  # noqa: E402
