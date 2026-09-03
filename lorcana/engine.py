"""Lorcana rules engine, scoped to the two decks in this project.

Turn structure: Ready -> Set (locations gain lore, start-of-turn triggers) ->
Draw (first player skips on turn 1) -> Main (actions until pass) -> end-of-turn
triggers. Win at 20 lore; a player forced to draw from an empty deck loses.
"""
import random

LORE_TO_WIN = 20


class _BoardDict(dict):
    """dict that counts mutations so my_chars()/my_locs() can cache safely.

    chars/locs are mutated from engine.py, abilities.py and schema.py.
    Centralising invalidation here means a future call site cannot silently
    stale the cache the way an explicit bump at each known mutation would.
    """
    __slots__ = ("version",)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.version = 0

    def __setitem__(self, k, v):
        self.version += 1
        super().__setitem__(k, v)

    def __delitem__(self, k):
        self.version += 1
        super().__delitem__(k)

    def pop(self, *a):
        self.version += 1
        return super().pop(*a)

    def popitem(self):
        self.version += 1
        return super().popitem()

    def clear(self):
        self.version += 1
        super().clear()

    def update(self, *a, **k):
        self.version += 1
        super().update(*a, **k)

    def setdefault(self, *a):
        self.version += 1
        return super().setdefault(*a)


class CharInPlay:
    __slots__ = ("uid", "card", "owner", "damage", "exerted", "turn_played",
                 "location", "under", "boosted")

    def __init__(self, uid, card, owner, turn_played, exerted=False):
        self.uid = uid
        self.card = card
        self.owner = owner
        self.damage = 0
        self.exerted = exerted
        self.turn_played = turn_played   # global turn counter when played (for drying ink)
        self.location = None             # uid of LocationInPlay or None
        self.under = []                  # cards shifted on top of (go to same zone on leave)
        self.boosted = []                # Boost: facedown cards put under this character

    def clone(self):
        c = CharInPlay(self.uid, self.card, self.owner, self.turn_played, self.exerted)
        c.damage = self.damage
        c.location = self.location
        c.under = list(self.under)
        c.boosted = list(self.boosted)
        return c


class ItemInPlay:
    __slots__ = ("uid", "card", "owner", "exerted", "turn_played", "under")

    def __init__(self, uid, card, owner, turn_played):
        self.uid = uid
        self.card = card
        self.owner = owner
        self.exerted = False
        self.turn_played = turn_played
        self.under = []               # faceup character cards (The Black Cauldron)

    def clone(self):
        i = ItemInPlay(self.uid, self.card, self.owner, self.turn_played)
        i.exerted = self.exerted
        i.under = list(self.under)
        return i


class LocInPlay:
    __slots__ = ("uid", "card", "owner", "damage", "under")

    def __init__(self, uid, card, owner):
        self.uid = uid
        self.card = card
        self.owner = owner
        self.damage = 0
        self.under = []               # Boost: facedown cards under this location

    def clone(self):
        l = LocInPlay(self.uid, self.card, self.owner)
        l.damage = self.damage
        l.under = list(self.under)
        return l


class PlayerState:
    __slots__ = ("deck", "hand", "discard", "ink_total", "ink_ready", "ink_cards", "lore")

    def __init__(self):
        self.deck = []        # list of Card, top of deck = end
        self.hand = []
        self.discard = []
        self.ink_total = 0
        self.ink_ready = 0
        self.ink_cards = []   # identities of inked cards (facedown; approximation: tracked)
        self.lore = 0

    def clone(self):
        p = PlayerState()
        p.deck = list(self.deck); p.hand = list(self.hand); p.discard = list(self.discard)
        p.ink_total = self.ink_total; p.ink_ready = self.ink_ready
        p.ink_cards = list(self.ink_cards); p.lore = self.lore
        return p


class Game:
    def __init__(self, deckA, deckB, seed=None, log=None):
        self.rng = random.Random(seed)
        self.players = [PlayerState(), PlayerState()]
        self.players[0].deck = list(deckA)
        self.players[1].deck = list(deckB)
        self.active = 0
        self.turn = 0            # global half-turn counter, +1 each player turn
        self.winner = None       # 0, 1, or None
        self.uid_seq = 0
        self.chars = _BoardDict()   # uid -> CharInPlay
        self.locs = _BoardDict()    # uid -> LocInPlay
        self._own_cache = None
        self.items = {0: [], 1: []}   # player -> list of ItemInPlay
        self.effects = []        # dicts: kind,target,amount,until(player) / 'eot'
        self.turn_flags = set()  # cleared at start of every turn
        self.cards_played = [0, 0]   # cards played this turn, per player
        self.action_ctx = None       # (player, card) while an action resolves
        self.challenge_ctx = None    # (attacker_uid, defender_uid) mid-challenge
        self._in_challenge_damage = False
        self._in_chosen_trigger = False
        self._in_action_watcher = False
        self.turn_discards = {0: 0, 1: 0}   # cards -> discard this turn (Milo)
        self.discounts = []      # dicts: owner, amount, filt, static(bool)
        self.log = log           # list to append log lines, or None

    # ---------- infrastructure ----------
    def clone(self):
        g = Game.__new__(Game)
        g.rng = random.Random(self.rng.random())
        g.players = [p.clone() for p in self.players]
        g.active = self.active; g.turn = self.turn; g.winner = self.winner
        g.uid_seq = self.uid_seq
        g.chars = _BoardDict((u, c.clone()) for u, c in self.chars.items())
        g.locs = _BoardDict((u, l.clone()) for u, l in self.locs.items())
        g._own_cache = None
        g.items = {0: [i.clone() for i in self.items[0]],
                   1: [i.clone() for i in self.items[1]]}
        g.effects = [dict(e) for e in self.effects]
        g.cards_played = list(self.cards_played)
        g.action_ctx = self.action_ctx
        g.challenge_ctx = self.challenge_ctx
        g._in_challenge_damage = self._in_challenge_damage
        g._in_chosen_trigger = self._in_chosen_trigger
        g._in_action_watcher = self._in_action_watcher
        g.turn_flags = set(self.turn_flags)
        g.turn_discards = dict(self.turn_discards)
        g.discounts = [dict(d) for d in self.discounts]
        g.log = None
        return g

    def emit(self, msg):
        if self.log is not None:
            self.log.append(f"  {msg}")

    def count(self, label, p):
        """Ability-trigger telemetry. Only the top-level analysis game carries
        a .trig Counter (set by analyze.py); MCTS clones don't, so search
        rollouts never pollute the counts."""
        t = getattr(self, "trig", None)
        if t is not None:
            t[(p, label)] += 1

    def next_uid(self):
        self.uid_seq += 1
        return self.uid_seq

    def _own(self):
        """Per-owner char/loc lists, rebuilt only when the board changes.

        These were millions of list rebuilds per game. Returned lists are
        SHARED and must not be mutated by callers; a board change builds fresh
        lists, so a reference taken before a banish still sees the pre-banish
        snapshot, matching the old copy-every-time semantics.
        """
        cv, lv = self.chars.version, self.locs.version
        c = self._own_cache
        if c is not None and c[0] == cv and c[1] == lv:
            return c
        c0, c1, l0, l1 = [], [], [], []
        for ch in self.chars.values():
            (c0 if ch.owner == 0 else c1).append(ch)
        for lo in self.locs.values():
            (l0 if lo.owner == 0 else l1).append(lo)
        c = (cv, lv, c0, c1, l0, l1)
        self._own_cache = c
        return c

    def my_chars(self, p):   return self._own()[2 + p]
    def my_locs(self, p):    return self._own()[4 + p]

    # ---------- derived stats (delegated to abilities) ----------
    def eff_strength(self, ch):
        return max(0, abilities.strength(self, ch))

    def eff_willpower(self, ch):
        return abilities.willpower(self, ch)

    def eff_lore(self, ch):
        return abilities.lore(self, ch)

    def eff_resist(self, ch):
        return abilities.resist(self, ch)

    def has_evasive(self, ch):
        return abilities.has_evasive(self, ch)

    def can_challenge_evasive(self, ch):
        return abilities.can_challenge_evasive(self, ch)

    def has_rush(self, ch):
        return abilities.has_rush(self, ch)

    def has_bodyguard(self, ch):
        return abilities.has_bodyguard(self, ch)

    def loc_lore(self, loc):
        return abilities.location_lore(self, loc)

    def eff_loc_willpower(self, loc):
        return abilities.location_willpower(self, loc)

    def is_dry(self, ch):
        return ch.turn_played < self.turn

    # ---------- setup ----------
    def start(self, mulligan_fn=None):
        for i, p in enumerate(self.players):
            self.rng.shuffle(p.deck)
            for _ in range(7):
                p.hand.append(p.deck.pop())
        if mulligan_fn:
            for i, p in enumerate(self.players):
                to_bottom = mulligan_fn(self, i)
                for card in to_bottom:
                    p.hand.remove(card)
                    p.deck.insert(0, card)
                for _ in range(len(to_bottom)):
                    p.hand.append(p.deck.pop())
                self.rng.shuffle(p.deck)
        self.turn = 0
        self.begin_turn(first=True)

    # ---------- turn flow ----------
    def begin_turn(self, first=False):
        self.turn += 1
        p = self.active
        self.turn_flags = set()
        self.cards_played = [0, 0]
        self.turn_discards = {0: 0, 1: 0}
        # Ready step
        self.players[p].ink_ready = self.players[p].ink_total
        from . import schema
        # "At the start of your turn" triggers, after ink is available so an
        # effect with a cost can actually pay it.
        schema.dispatch_turn_start(self, p)
        for ch in self.my_chars(p):
            if any(e["kind"] == "no_ready" and e["target"] == ch.uid
                   for e in self.effects):
                self.effects = [e for e in self.effects
                                if not (e["kind"] == "no_ready" and e["target"] == ch.uid)]
                continue
            # Standing restrictions are re-evaluated each turn and are never
            # consumed (Demona - Betrayer of the Clan STONE BY DAY).
            if schema.static_no_ready(self, ch):
                continue
            ch.exerted = False
        for it in self.items[p]:
            if schema.blocks_item_ready(self, it, p):
                continue
            it.exerted = False
        # expire "until start of [p]'s next turn" effects
        self.effects = [e for e in self.effects if e.get("until") != p]
        self.emit(f"-- Turn {self.turn}: Player {p} readies --")
        # Set step: start-of-turn triggers, then location lore
        abilities.start_of_turn(self, p)
        if self.winner is not None:
            return
        for loc in list(self.my_locs(p)):
            gained = self.loc_lore(loc)
            if gained:
                self.gain_lore(p, gained, f"{loc.card.name} (location)")
                if self.winner is not None:
                    return
        # Draw step
        if not first:
            self.draw(p, 1, forced=True)

    def end_turn(self):
        p = self.active
        abilities.end_of_turn(self, p)
        # expire this-turn effects & non-static discounts
        self.effects = [e for e in self.effects if e.get("until") != "eot"]
        self.discounts = [d for d in self.discounts if d.get("static")]
        self.active = 1 - p
        if self.winner is None:
            self.begin_turn()

    # ---------- primitives ----------
    def draw(self, p, n=1, forced=False):
        pl = self.players[p]
        for _ in range(n):
            if not pl.deck:
                if forced:
                    self.winner = 1 - p
                    self.emit(f"Player {p} decks out!")
                return
            pl.hand.append(pl.deck.pop())
        if n:
            self.emit(f"P{p} draws {n}")

    def gain_lore(self, p, n, why=""):
        self.players[p].lore += n
        self.emit(f"P{p} +{n} lore ({why}) -> {self.players[p].lore}")
        if self.players[p].lore >= LORE_TO_WIN and self.winner is None:
            self.winner = p

    def deal_damage(self, ch, amount, apply_resist=True, challenge=False):
        from . import schema as _sch
        if getattr(self, "_in_challenge_damage", False) \
                and _sch.takes_no_challenge_damage(self, ch):
            self.emit(f"{ch.card.base_name} takes no damage from challenges")
            return
        if amount <= 0:
            return
        # replacement / prevention effects (Lilo EXTRA LAYERS, Hercules EVER
        # VIGILANT). challenge=True marks damage dealt during a challenge.
        amount = abilities.replace_damage(self, ch, amount, challenge)
        if amount <= 0:
            return
        if apply_resist:
            amount = max(0, amount - self.eff_resist(ch))
        if amount <= 0:
            return
        ch.damage += amount
        self.emit(f"{ch.card.base_name}(P{ch.owner}) takes {amount} dmg "
                  f"({ch.damage}/{self.eff_willpower(ch)})")
        # "Whenever one of your actions deals damage to an opposing character"
        # watchers. Guarded against re-entry so a watcher that itself deals
        # damage cannot retrigger itself.
        if self.action_ctx and not self._in_action_watcher:
            ap, _acard = self.action_ctx
            if ch.owner != ap and ch.damage < self.eff_willpower(ch):
                from . import schema
                self._in_action_watcher = True
                try:
                    schema.dispatch_action_damage(self, ap, ch)
                finally:
                    self._in_action_watcher = False
        if ch.uid in self.chars and ch.damage >= self.eff_willpower(ch):
            self.banish_char(ch)

    def banish_char(self, ch, cause="damage"):
        if ch.uid not in self.chars:
            return
        if abilities.replace_banish(self, ch):
            return
        del self.chars[ch.uid]
        self.players[ch.owner].discard.append(ch.card)
        self.players[ch.owner].discard.extend(ch.under)
        self.players[ch.owner].discard.extend(ch.boosted)
        self.turn_discards[ch.owner] = self.turn_discards.get(ch.owner, 0) +             1 + len(ch.under) + len(ch.boosted)
        self.emit(f"{ch.card.name} (P{ch.owner}) banished")
        abilities.on_banish(self, ch, cause)

    def banish_loc(self, loc):
        if loc.uid not in self.locs:
            return
        del self.locs[loc.uid]
        for ch in self.chars.values():
            if ch.location == loc.uid:
                ch.location = None
        self.players[loc.owner].discard.append(loc.card)
        self.players[loc.owner].discard.extend(loc.under)
        from . import schema as _sch
        _sch.dispatch_location_banished(self, loc)
        self.emit(f"{loc.card.name} (P{loc.owner}) banished")

    def banish_item(self, item):
        """Banish an item in play, firing item-banish triggers (TAKE THAT!)."""
        p = item.owner
        if item in self.items[p]:
            self.items[p].remove(item)
        self.players[p].discard.append(item.card)
        self.players[p].discard.extend(item.under)
        self.emit(f"{item.card.name} (P{p}) banished")
        abilities.on_item_banished(self, item)

    def discard_card(self, p, card):
        """Send a card from hand to discard, tracking count (Milo) and firing
        discard triggers (Look What You've Done)."""
        self.players[p].discard.append(card)
        self.turn_discards[p] = self.turn_discards.get(p, 0) + 1
        abilities.on_discard(self, p, card)

    def damage_loc(self, loc, dmg):
        if dmg <= 0:
            return
        dmg = max(0, dmg - self.eff_loc_resist(loc))
        if dmg <= 0:
            return
        loc.damage += dmg
        self.emit(f"{loc.card.base_name} takes {dmg}")
        if loc.damage >= self.eff_loc_willpower(loc):
            self.banish_loc(loc)

    def eff_loc_resist(self, loc):
        return abilities.location_resist(self, loc)

    def check_banish(self, ch):
        if ch.uid in self.chars and ch.damage >= self.eff_willpower(ch):
            self.banish_char(ch)

    # ---------- cost computation ----------
    def play_cost(self, p, card):
        cost = card.cost
        cost -= abilities.static_discount(self, p, card)
        for d in self.discounts:
            if d["owner"] == p and not d.get("static") and abilities.discount_applies(d, card):
                cost -= d["amount"]
        return max(0, cost)

    def consume_discounts(self, p, card):
        remaining = []
        for d in self.discounts:
            if d["owner"] == p and not d.get("static") and abilities.discount_applies(d, card):
                continue  # consumed
            remaining.append(d)
        self.discounts = remaining

    def pay_ink(self, p, n):
        assert self.players[p].ink_ready >= n
        self.players[p].ink_ready -= n

    # ---------- action generation ----------
    def legal_actions(self):
        p = self.active
        pl = self.players[p]
        acts = [("pass",)]
        seen_names = set()
        # ink (once per turn)
        if "inked" not in self.turn_flags:
            for card in pl.hand:
                if card.inkable and ("ink", card.name) not in seen_names:
                    acts.append(("ink", card.name))
                    seen_names.add(("ink", card.name))
            # Moana - Curious Explorer ANCESTRAL LEGACY: you may ink inkable
            # cards from your discard as well (still just one ink per turn).
            if abilities.can_ink_from_discard(self, p):
                disc_seen = set()
                for card in pl.discard:
                    if card.inkable and card.name not in disc_seen:
                        acts.append(("ink", card.name, "discard"))
                        disc_seen.add(card.name)
        # plays
        for card in pl.hand:
            key = ("play", card.name)
            if key in seen_names:
                continue
            seen_names.add(key)
            cost = self.play_cost(p, card)
            for params in abilities.play_param_options(self, p, card):
                pd = dict(params)
                base = abilities.shift_cost(card) if "shift" in pd else cost
                if pl.ink_ready >= base:
                    acts.append(("play", card.name, params))
        # sing / sing together (generic, Phase 1: Singer N counts as cost N;
        # Sing Together threshold comes from the printed keyword)
        for card in pl.hand:
            if not card.is_song:
                continue
            singers = [c for c in self.my_chars(p)
                       if not c.exerted and self.is_dry(c)
                       and c.card.singer_value >= card.cost]
            if singers and ("sing", card.name) not in seen_names:
                acts.append(("sing", card.name))
                seen_names.add(("sing", card.name))
            st_cost = card.sing_together_cost
            if st_cost is not None:
                pool = [c for c in self.my_chars(p) if not c.exerted and self.is_dry(c)]
                if sum(c.card.singer_value for c in pool) >= st_cost \
                        and ("singt", card.name) not in seen_names:
                    acts.append(("sing_together", card.name))
                    seen_names.add(("singt", card.name))
        # quests. Reckless characters can't quest (generic, Phase 1). A character
        # questing at Sleepy Hollow MAY banish it for 2 lore + Evasive -> expose
        # both choices as separate actions.
        # Build the no_quest target set once instead of rescanning self.effects
        # once per character.
        no_quest = {e["target"] for e in self.effects if e["kind"] == "no_quest"}
        for ch in self.my_chars(p):
            from . import schema as _sch
            if _sch.blocks_quest_challenge(self, ch):
                continue
            if _sch.blocks_quest_by_classification(self, ch):
                continue
            if not ch.exerted and self.is_dry(ch) and not abilities.has_reckless(self, ch) \
                    and ("no_quest", ch.uid) not in self.turn_flags \
                    and ch.uid not in no_quest \
                    and pl.ink_ready >= abilities.action_ink_surcharge(self, ch):
                loc = self.locs.get(ch.location)
                if loc and loc.card.name == "Sleepy Hollow - The Bridge":
                    acts.append(("quest", ch.uid, "sh_keep"))
                    acts.append(("quest", ch.uid, "sh_banish"))
                elif ch.card.name == "Rapunzel & Flynn Rider - Unlikely Pair":
                    # CLEVER SWAP: expose each distinct current hand card as a
                    # discard choice, plus "discard the card just drawn".
                    for nm in abilities.clever_swap_options(self, p):
                        acts.append(("quest", ch.uid, ("swap", nm)))
                else:
                    acts.append(("quest", ch.uid))
        # challenges
        opp = 1 - p

        reckless_must_challenge = False
        for ch in self.my_chars(p):
            if ch.exerted or (not self.is_dry(ch) and not self.has_rush(ch)):
                continue
            if abilities.cant_challenge(self, ch):
                continue
            if pl.ink_ready < abilities.action_ink_surcharge(self, ch):
                continue
            targets = self.challenge_targets(ch)
            if targets and abilities.has_reckless(self, ch):
                reckless_must_challenge = True
            for kind, uid in targets:
                acts.append(("challenge", ch.uid, kind, uid))
        # Reckless: must challenge each turn if able -> you may not end the turn
        # while a ready Reckless character has a legal challenge.
        if reckless_must_challenge:
            acts = [a for a in acts if a[0] != "pass"]
        # Boost N: once during your turn, pay N ink to put the top card of your
        # deck facedown under this character/location. (generic, from printed text)
        for ch in self.my_chars(p):
            n = abilities.boost_cost(ch.card)
            if n is not None and pl.ink_ready >= n and pl.deck \
                    and ("boost", ch.uid) not in self.turn_flags:
                acts.append(("boost", "char", ch.uid))
        for loc in self.my_locs(p):
            n = abilities.boost_cost(loc.card)
            if n is not None and pl.ink_ready >= n and pl.deck \
                    and ("boost", loc.uid) not in self.turn_flags:
                acts.append(("boost", "loc", loc.uid))
        # activated abilities exposed as decisions (e.g. Aladdin's granted
        # ONLY THE BOLD "exert -- gain 1 lore" on Reckless characters)
        for a in abilities.activated_actions(self, p):
            acts.append(a)
        # moves (pay move cost); a character may not move to its current location.
        # Moving to Zootopia MAY trigger draw-then-discard (once/turn) -> expose
        # both choices when the trigger is still available and drawing is possible.
        for ch in self.my_chars(p):
            for loc in self.my_locs(p):
                from . import schema as _schema
                _free = _schema.location_free_move_for(self, loc, ch)
                _cost = 0 if _free else loc.card.move_cost
                if ch.location != loc.uid and _cost is not None \
                        and pl.ink_ready >= _cost:
                    if loc.card.name == "Zootopia - Police Headquarters" \
                            and ("zoo", loc.uid) not in self.turn_flags \
                            and pl.deck:
                        acts.append(("move", ch.uid, loc.uid, "zoo_draw"))
                        acts.append(("move", ch.uid, loc.uid, "zoo_skip"))
                    else:
                        acts.append(("move", ch.uid, loc.uid))
        return acts

    def challenge_targets(self, attacker):
        from . import schema
        if schema.blocks_quest_challenge(self, attacker):
            return []
        p = attacker.owner
        opp = 1 - p
        char_targets = []
        for dc in self.my_chars(opp):
            if not dc.exerted and not abilities.can_challenge_ready(self, attacker, dc):
                continue
            if self.has_evasive(dc) and not self.can_challenge_evasive(attacker):
                continue
            char_targets.append(dc)
        # Bodyguard restriction (characters only)
        bg = [c for c in char_targets if self.has_bodyguard(c)]
        if bg:
            char_targets = bg
        out = [("char", c.uid) for c in char_targets]
        from . import schema
        for loc in self.my_locs(opp):
            if any(e["kind"] == "no_challenge" and e["target"] == loc.uid for e in self.effects):
                continue
            # A location may itself gain Evasive (Game Preserve - Protected
            # Land), which restricts who can challenge it.
            if schema.static_location_keyword(self, loc, "evasive") \
                    and not self.can_challenge_evasive(attacker):
                continue
            out.append(("loc", loc.uid))
        return out

    # ---------- action application ----------
    def apply(self, action):
        p = self.active
        pl = self.players[p]
        kind = action[0]

        if kind == "pass":
            self.end_turn()
            return

        if kind == "ink":
            from_discard = len(action) > 2 and action[2] == "discard"
            if from_discard:
                # Moana ANCESTRAL LEGACY: pull the inked card from the discard.
                card = next(c for c in pl.discard if c.name == action[1])
                pl.discard.remove(card)
            else:
                card = self._hand_card(p, action[1])
                pl.hand.remove(card)
            pl.ink_cards.append(card)
            pl.ink_total += 1
            pl.ink_ready += 1
            self.turn_flags.add("inked")
            self.emit(f"P{p} inks {card.name}" + (" (from discard)" if from_discard else "")
                      + f" ({pl.ink_ready}/{pl.ink_total})")
            return

        if kind == "play":
            card = self._hand_card(p, action[1])
            params = dict(action[2]) if len(action) > 2 and action[2] else {}
            self._play_card(p, card, params)
            return

        if kind == "sing":
            card = self._hand_card(p, action[1])
            singers = [c for c in self.my_chars(p)
                       if not c.exerted and self.is_dry(c)
                       and c.card.singer_value >= card.cost]
            singer = max(singers, key=lambda c: (-self.eff_lore(c), c.card.cost))
            singer.exerted = True
            self.emit(f"{singer.card.base_name} sings {card.name}")
            # Play the song FIRST, then resolve sing triggers (MASH-UP,
            # KARAOKE QUEEN). Resolving on_sing first let a MASH-UP-chained
            # song (e.g. Strike A Good Match) discard the still-in-hand sung
            # card, which either crashed _play_card's hand.remove or, with a
            # tolerant remove, duplicated the card into the discard.
            self._play_card(p, card, {}, free=True, sung=True)
            if singer.uid in self.chars:
                abilities.on_sing(self, p, singer, card)
            return

        if kind == "sing_together":
            card = self._hand_card(p, action[1])
            need = card.sing_together_cost or card.cost
            pool = sorted([c for c in self.my_chars(p) if not c.exerted and self.is_dry(c)],
                          key=lambda c: (self.eff_lore(c), -c.card.singer_value))
            total, chosen = 0, []
            for c in pool:
                if total >= need:
                    break
                chosen.append(c); total += c.card.singer_value
            for c in chosen:
                c.exerted = True
            self.emit(f"Sing Together: {[c.card.base_name for c in chosen]} sing {card.name}")
            self._play_card(p, card, {}, free=True, sung=True)
            return

        if kind == "quest":
            ch = self.chars[action[1]]
            surcharge = abilities.action_ink_surcharge(self, ch)
            if surcharge:
                if self.players[p].ink_ready < surcharge:
                    return
                self.pay_ink(p, surcharge)
                self.emit(f"{ch.card.base_name} pays {surcharge} ink to act (LOW BATTERIES)")
            choice = action[2] if len(action) > 2 else None
            ch.exerted = True
            self.gain_lore(p, self.eff_lore(ch), f"{ch.card.base_name} quests")
            if self.winner is None:
                abilities.on_quest(self, ch, sh_banish=(choice == "sh_banish"),
                                   choice=choice)
            return

        if kind == "challenge":
            atk = self.chars[action[1]]
            surcharge = abilities.action_ink_surcharge(self, atk)
            if surcharge:
                if self.players[p].ink_ready < surcharge:
                    return
                self.pay_ink(p, surcharge)
                self.emit(f"{atk.card.base_name} pays {surcharge} ink to act (LOW BATTERIES)")
            self.turn_flags.add(("challenged", p))
            self._challenge(atk, action[2], action[3])
            return

        if kind == "boost":
            what, uid = action[1], action[2]
            obj = self.chars[uid] if what == "char" else self.locs[uid]
            n = abilities.boost_cost(obj.card)
            self.pay_ink(p, n)
            card = pl.deck.pop()
            if what == "char":
                obj.boosted.append(card)
            else:
                obj.under.append(card)
            self.turn_flags.add(("boost", uid))
            self.emit(f"P{p} boosts {obj.card.base_name} "
                      f"({len(obj.boosted) if what=='char' else len(obj.under)} under)")
            abilities.on_boost(self, p, obj)
            return

        if kind == "activate":
            abilities.apply_activated(self, p, action)
            return

        if kind == "move":
            ch = self.chars[action[1]]
            loc = self.locs[action[2]]
            choice = action[3] if len(action) > 3 else None
            from . import schema as _schema
            if not _schema.location_free_move_for(self, loc, ch):
                self.pay_ink(p, loc.card.move_cost)
            ch.location = loc.uid
            self.emit(f"{ch.card.base_name} moves to {loc.card.base_name}")
            abilities.on_move(self, ch, loc, zoo_draw=(choice != "zoo_skip"))
            return

        raise ValueError(f"unknown action {action}")

    def _challenge(self, attacker, kind, uid):
        attacker.exerted = True
        # "While being challenged" statics (Enchantress TRUE FORM) read this.
        self.challenge_ctx = (attacker.uid, uid)
        try:
            return self._challenge_inner(attacker, kind, uid)
        finally:
            self.challenge_ctx = None

    def _challenge_inner(self, attacker, kind, uid):
        self._in_challenge_damage = True
        try:
            return self._challenge_damage(attacker, kind, uid)
        finally:
            self._in_challenge_damage = False

    def _challenge_damage(self, attacker, kind, uid):
        # Dale SPIKE SUIT: your characters deal challenge damage with their
        # Willpower instead of their Strength (abilities.challenge_damage).
        atk = abilities.challenge_damage(self, attacker) \
            + abilities.challenger_bonus(self, attacker)
        if kind == "loc":
            loc = self.locs.get(uid)
            if loc is None:
                return
            self.emit(f"{attacker.card.base_name} challenges {loc.card.base_name} for {atk}")
            dmg = max(0, atk - abilities.location_resist(self, loc))
            loc.damage += dmg
            if loc.damage >= self.eff_loc_willpower(loc):
                self.banish_loc(loc)
            return
        defender = self.chars.get(uid)
        if defender is None:
            return
        self.emit(f"{attacker.card.base_name}({atk}) challenges "
                  f"{defender.card.base_name}({abilities.challenge_damage(self, defender)})")
        # KID-TASTROPHE!: no damage is dealt in that challenge
        if abilities.kid_tastrophe(self, attacker, defender):
            self.emit(f"KID-TASTROPHE! banishes {defender.card.base_name}")
            self.banish_char(defender, cause="challenge")
            return
        # Scrooge ON THE MOVE: challenged -> return to hand, no damage dealt.
        if abilities.defender_returns_when_challenged(self, attacker, defender):
            return
        dmg_to_def = abilities.replace_damage(self, defender, atk, True)
        dmg_to_def = max(0, dmg_to_def - self.eff_resist(defender))
        dmg_to_atk = abilities.replace_damage(self, attacker,
                                              abilities.challenge_damage(self, defender), True)
        dmg_to_atk = max(0, dmg_to_atk - self.eff_resist(attacker))
        # Beast DYNAMIC MANEUVER / Rafiki ANCIENT SKILLS: the attacker takes
        # no damage from this challenge.
        if abilities.attacker_takes_no_challenge_damage(self, attacker, defender):
            dmg_to_atk = 0
        defender.damage += dmg_to_def
        attacker.damage += dmg_to_atk
        def_dies = defender.damage >= self.eff_willpower(defender)
        atk_dies = attacker.damage >= self.eff_willpower(attacker)
        # EVEN THE SCORE / PUNY PIRATE! fire on the challenge outcome itself,
        # including mutual trades (unlike after_challenge_banish, which only
        # fires for a clean one-sided banish).
        abilities.on_challenge_banish(self, attacker, defender, atk_dies, def_dies)
        if def_dies:
            self.banish_char(defender, cause="challenge")
        if atk_dies:
            self.banish_char(attacker, cause="challenge")
        # CHEAP SHOT (banisher must survive and be at Nomanisan)
        if def_dies and not atk_dies:
            abilities.after_challenge_banish(self, attacker)
        elif atk_dies and not def_dies:
            abilities.after_challenge_banish(self, defender)
        abilities.on_challenge(self, attacker, defender)

    def _hand_card(self, p, name):
        for c in self.players[p].hand:
            if c.name == name:
                return c
        raise ValueError(f"{name} not in hand of P{p}")

    def _play_card(self, p, card, params, free=False, sung=False):
        pl = self.players[p]
        paid = 0
        shift_uid = params.get("shift")
        if not free:
            if shift_uid is not None:
                paid = abilities.shift_cost(card)
            else:
                paid = self.play_cost(p, card)
                self.consume_discounts(p, card)
                abilities.consume_static(self, p, card)
            self.pay_ink(p, paid)
        if card in pl.hand:
            pl.hand.remove(card)
        self.cards_played[p] += 1
        self.emit(f"P{p} plays {card.name}" + (" (shift)" if shift_uid else "") +
                  (f" paying {paid}" if not free else " (free)"))

        obj = None
        if card.is_character:
            if shift_uid is not None and shift_uid in self.chars:
                base = self.chars[shift_uid]
                # Duo Shift: the second character is also absorbed
                other_uid = params.get("duo_other")
                if other_uid is not None and other_uid in self.chars:
                    other = self.chars.pop(other_uid)
                    base.under.append(other.card)
                    base.under.extend(other.under)
                    self.players[other.owner].discard.extend(other.boosted)
                    self.emit(f"Duo Shift absorbs {other.card.base_name}")
                base.under.append(base.card)
                base.card = card          # same character: keeps damage/exert/dry/location
                obj = base
                if params.get("temporary"):
                    # Temporary Shift: revert to the previous form at end of turn
                    self.effects.append({"kind": "temp_shift", "target": base.uid,
                                         "amount": 0, "until": "eot"})
            else:
                exerted = bool(params.get("exerted"))
                obj = CharInPlay(self.next_uid(), card, p, self.turn, exerted)
                # Characters that arrive already damaged (Zeus - Defiant God).
                from . import schema as _schema
                obj.damage += _schema.static_enters_damage(card)
                self.chars[obj.uid] = obj
        elif card.is_location:
            obj = LocInPlay(self.next_uid(), card, p)
            self.locs[obj.uid] = obj
        elif card.is_item:
            obj = ItemInPlay(self.next_uid(), card, p, self.turn)
            from . import schema
            if schema.static_enters_exerted(card):
                obj.exerted = True
                self.emit(f"{card.name} enters play exerted")
            self.items[p].append(obj)
        # actions resolve then discard
        abilities.on_play(self, p, card, obj, params)
        if card.is_action:
            pl.discard.append(card)
        # record what type was played this turn (for Pocahontas / Carl triggers).
        # Recorded AFTER on_play so a card's own play-trigger doesn't see itself.
        if card.is_character:
            self.turn_flags.add(("played_char", p))
            if obj is not None:
                self.turn_flags.add(("played_char_uid", obj.uid))
        elif card.is_location:
            self.turn_flags.add(("played_loc", p))
        # "whenever you pay N ink or less to play a card" triggers (free counts as 0)
        abilities.on_pay_to_play(self, p, card, paid if not free else 0)


# Imported at the bottom, not the top: engine <-> abilities is a genuine cycle
# (abilities needs engine.LocInPlay). Binding here works because abilities.py
# has no module-level imports of its own, so it loads cleanly once engine's
# classes are defined. Previously each of the 24 call sites re-ran the import
# machinery on every call -- millions of _handle_fromlist calls per search.
from . import abilities  # noqa: E402
