"""Baseline policies: mulligan heuristic, random player, greedy player.
The greedy player is also the MCTS rollout policy."""
import random


def default_mulligan(game, p):
    """Bottom cards costing 5+, except live combo pieces.

    The flat cost rule is a reasonable proxy for 'too slow to keep', but it is
    exactly wrong for a hand that already holds a shift card AND the base it
    wants: bottoming either half turns a discounted two-turn tempo play into a
    dead card. Nothing is in play at mulligan time, so hand-internal pairs are
    the only inference available -- and the only one this rule needs.
    """
    from . import combos
    hand = game.players[p].hand
    keeps = combos.combo_mulligan_keeps(hand)
    return [c for c in hand if c.cost >= 5 and c.name not in keeps]


def random_policy(game, rng):
    return rng.choice(game.legal_actions())


def greedy_policy(game, rng, epsilon=0.10):
    """Priority-ordered heuristic with epsilon randomness."""
    acts = game.legal_actions()
    if rng.random() < epsilon:
        return rng.choice(acts)
    p = game.active

    def act_of(kind):
        return [a for a in acts if a[0] == kind]

    # 1. lethal quest first. A Sleepy Hollow banish ('sh_banish') adds +2 lore,
    #    so account for that when checking for lethal.
    quests = act_of("quest")
    lore_now = game.players[p].lore

    def quest_lore(a):
        base = game.eff_lore(game.chars[a[1]])
        if len(a) > 2 and a[2] == "sh_banish":
            base += 2
        return base

    if quests:
        best_q = max(quests, key=quest_lore)
        if lore_now + quest_lore(best_q) >= 20:
            return best_q

    # 1b. free lore from activated abilities (ONLY THE BOLD): a Reckless body
    #     that can't quest converting an exert into 1 lore is pure upside.
    #     Guidebook: draw 2 for 1 ink is taken when hand is thin.
    for a in acts:
        if a[0] == "activate" and a[1] == "only_the_bold":
            return a
    # Dumbo BREAKING RECORDS: 1 ink -> draw + 1 lore is always worth it
    for a in acts:
        if a[0] == "activate" and a[1] == "breaking_records":
            return a
    # Gyro-Evac TAKE HER UP: free-ish evasion for our best quester
    for a in acts:
        if a[0] == "activate" and a[1] == "gyro_evasive":
            return a
    # Look What You've Done: replaying it from the discard is pure value
    for a in acts:
        if a[0] == "activate" and a[1] == "lwyd_from_discard":
            return a
    # Angel GOOD AIM: discard to deal 2 -- only with a fat hand
    for a in acts:
        if a[0] == "activate" and a[1] == "good_aim" and len(game.players[p].hand) >= 4:
            return a
    # Rapunzel THE CALL OF ADVENTURE: discarding a card for +1 Strength and
    # Evasive is card disadvantage, so it is only taken with a surplus hand
    # (same bar as GOOD AIM) and only while she is still unprotected -- once
    # Evasive is up, a second activation this turn is impossible anyway.
    for a in acts:
        if a[0] == "activate" and a[1] == "call_of_adventure" \
                and len(game.players[p].hand) >= 4:
            ch = game.chars.get(a[2])
            if ch is not None and not game.has_evasive(ch):
                return a
    for a in acts:
        if a[0] == "activate" and a[1] == "guidebook" and len(game.players[p].hand) <= 4:
            return a
    # Generic schema-driven activations ("activate", "schema", uid, index).
    # The hand-written keys above each encode a bespoke judgement; data-driven
    # entries have no such rule, so apply one conservative default: take the
    # ability when its whole cost is an exert we are not otherwise using, and
    # skip anything that spends ink, cards or bodies. Without this they would
    # sit in the action space and only ever be explored at random by MCTS.
    for a in acts:
        if a[0] != "activate" or a[1] != "schema":
            continue
        from . import schema
        obj = game.chars.get(a[2]) \
            or next((x for x in game.items[p] if x.uid == a[2]), None) \
            or game.locs.get(a[2])
        if obj is None:
            continue
        ents = schema.activated_entries(obj.card.name)
        if a[3] >= len(ents):
            continue
        cost = ents[a[3]].get("cost") or {}
        if cost.get("ink") or cost.get("discard") \
                or cost.get("banish_self") or cost.get("banish_own_char"):
            continue
        # exerting a character costs us a quest or a challenge; only free for
        # items and locations, which have nothing else to do with the exert.
        if hasattr(obj, "damage"):
            continue
        return a

    # 2. ink once per turn: highest-cost inkable (prefer duplicates implicitly).
    #    An ("ink", name, "discard") action (Moana ANCESTRAL LEGACY) draws from
    #    the discard; look the cost up there. Tie-break toward discard-sourced
    #    inks, since spending a dead card is strictly better than a hand card.
    inks = act_of("ink")
    if inks:
        from . import combos
        protected = combos.combo_protected_names(game, p)

        def ink_key(a):
            from_discard = len(a) > 2 and a[2] == "discard"
            zone = game.players[p].discard if from_discard else game.players[p].hand
            cost = next((c.cost for c in zone if c.name == a[1]), 0)
            # Combo pieces sort BEFORE cost: inking the base a held shift card
            # wants strands that card as an overcosted body, which costs far
            # more than the one point of curve given up here. This is the
            # concrete fix for the bias deckbuild.py's docstring already admits
            # to ("will ink away payoffs it can't plan around"). Discard-sourced
            # ink is never a combo piece -- that card is already dead.
            safe = 0 if (not from_discard and a[1] in protected) else 1
            return (safe, cost, 1 if from_discard else 0)
        return max(inks, key=ink_key)

    # 2b. take an available Shift. greedy has no lookahead, so this is the one
    #     moment a multi-turn payoff is visible as a legal action: the base is
    #     down, the ink is up, and the shifted body keeps its dry state (so it
    #     can act this turn). Deferring risks losing the base to removal before
    #     the window reopens. Placed after ink and before challenges because a
    #     shifted character is often the best attacker on the board -- and
    #     because section 4 below ranks plays by PRINTED cost, which prices a
    #     shift at its undiscounted cost and so ranks it against the wrong
    #     alternatives.
    #     ASSUMPTION: shifting is never worse than holding. Wrong for a minority
    #     of cards -- shifting off a buffed or Bodyguard body, or a Duo Shift
    #     that eats two useful characters to make one. Revisit if the policy
    #     tests regress on Duo-heavy shells.
    #     NB: test for `is not None`, not truthiness -- uid 0 is a valid target
    #     and silently disabled this whole tier when the base happened to be the
    #     first character created in the game.
    shifts = [a for a in acts
              if a[0] == "play" and len(a) > 2 and a[2]
              and dict(a[2]).get("shift") is not None]
    if shifts:
        def shift_key(a):
            card = next((c for c in game.players[p].hand if c.name == a[1]), None)
            return (card.lore, card.cost) if card else (0, 0)
        return max(shifts, key=shift_key)

    # 3. favorable challenges: kill the defender and either survive or trade up
    from . import abilities, combos
    shift_bases = combos.live_shift_bases(game, p)
    COMBO_BASE_PENALTY = 6
    best_chal, best_score = None, 0
    for a in act_of("challenge"):
        att = game.chars[a[1]]
        if a[2] == "char":
            d = game.chars.get(a[3])
            if d is None:
                continue
            atk = game.eff_strength(att) + abilities.challenger_bonus(game, att)
            dmg_out = max(0, atk - game.eff_resist(d))
            dmg_in = max(0, game.eff_strength(d) - game.eff_resist(att))
            if att.card.name == "Beast - Snowfield Troublemaker" and att.location is not None:
                dmg_in = 0
            kills = d.damage + dmg_out >= game.eff_willpower(d)
            dies = att.damage + dmg_in >= game.eff_willpower(att)
            if kills and not dies:
                score = 10 + game.eff_lore(d)
            elif kills and dies and game.eff_lore(d) > game.eff_lore(att):
                score = 3
            else:
                score = 0
            # Don't feed a character a held shift card is waiting on into a
            # trade. Calibrated as a discount, not a veto: a clean kill (10+)
            # survives the penalty and is still taken, while a mutual trade (3)
            # drops below the threshold and is declined.
            if score and att.uid in shift_bases:
                score -= COMBO_BASE_PENALTY
            if score > best_score:
                best_chal, best_score = a, score
        else:
            loc = game.locs.get(a[3])
            if loc is None:
                continue
            atk = game.eff_strength(att) + abilities.challenger_bonus(game, att)
            if loc.damage + atk >= loc.card.willpower and game.loc_lore(loc) >= 1:
                score = 5 + game.loc_lore(loc)
                if score > best_score:
                    best_chal, best_score = a, score
    if best_chal:
        return best_chal

    # 3b. Boost: put a card under a character/location when it pays off --
    #     Scrooge/Hercules/Counting House grow, Aladdin turns on ONLY THE BOLD,
    #     Sulley & Boo banks free replays, Ariel enables COMMAND PERFORMANCE.
    #     Only boost with ink we would otherwise float.
    boosts = act_of("boost")
    if boosts:
        def boost_val(a):
            obj = game.chars.get(a[2]) if a[1] == "char" else game.locs.get(a[2])
            if obj is None:
                return 0
            n = obj.card.name
            if n == "Aladdin - Barreling Through":
                # first card under Aladdin switches on the lore engine
                return 100 if not obj.boosted else 5
            if n == "Scrooge McDuck - Ghostly Ebenezer":
                return 40   # +1/+1 permanently
            if n == "Scrooge's Counting House - Ebenezer's Office":
                return 35   # +1 lore/turn permanently
            if n == "Hercules - Spectral Demigod":
                return 30 if not obj.boosted else 0   # +3 str once
            if n == "Sulley & Boo - Scare Buddies":
                return 20   # banks a free replay on banish
            if n == "Ariel - Ethereal Voice":
                return 15 if not obj.boosted else 0
            return 1
        best_b = max(boosts, key=boost_val)
        if boost_val(best_b) >= 15:
            return best_b

    # 4. play the most expensive playable card (prefer characters)
    plays = act_of("play") + act_of("sing") + act_of("sing_together")
    if plays:
        def play_val(a):
            for c in game.players[p].hand:
                if c.name == a[1]:
                    return (c.cost + (0.5 if c.is_character else 0)
                            + (2 if a[0].startswith("sing") else 0))
            return 0
        best = max(plays, key=play_val)
        if play_val(best) > 0:
            return best

    # 5. value-generating location moves (before questing, so Sleepy Hollow /
    #    Elsa lore bonuses apply to this turn's quests)
    for a in act_of("move"):
        ch, loc = game.chars[a[1]], game.locs[a[2]]
        if ch.card.name == "Elsa - Ice Artisan" and ch.location is None:
            return a  # +3 lore
        occupied = any(c.location == loc.uid for c in game.my_chars(p))
        if not occupied and loc.card.name == "Illuminary Tunnels - Linked Caverns":
            return a  # unlock SUBTERRANEAN NETWORK lore
        if not occupied and loc.card.base_name == "Sleepy Hollow" \
                and not ch.exerted and game.is_dry(ch):
            return a  # quest there for +2 lore
        if ch.card.name == "Beast - Snowfield Troublemaker" and ch.location is None \
                and not ch.exerted:
            return a  # enable DYNAMIC MANEUVER

    # 6. quest with everything. Default to NOT banishing Sleepy Hollow (lethal
    #    was already handled above), so skip 'sh_banish' variants here.
    if quests:
        keep = [a for a in quests if not (len(a) > 2 and a[2] == "sh_banish")]
        pool = keep or quests
        return max(pool, key=lambda a: game.eff_lore(game.chars[a[1]]))

    return ("pass",)
