"""Baseline policies: mulligan heuristic, random player, greedy player.
The greedy player is also the MCTS rollout policy."""
import random


def default_mulligan(game, p):
    """Bottom cards costing 5+."""
    return [c for c in game.players[p].hand if c.cost >= 5]


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
    for a in acts:
        if a[0] == "activate" and a[1] == "guidebook" and len(game.players[p].hand) <= 4:
            return a

    # 2. ink once per turn: highest-cost inkable (prefer duplicates implicitly).
    #    An ("ink", name, "discard") action (Moana ANCESTRAL LEGACY) draws from
    #    the discard; look the cost up there. Tie-break toward discard-sourced
    #    inks, since spending a dead card is strictly better than a hand card.
    inks = act_of("ink")
    if inks:
        def ink_key(a):
            from_discard = len(a) > 2 and a[2] == "discard"
            zone = game.players[p].discard if from_discard else game.players[p].hand
            cost = next((c.cost for c in zone if c.name == a[1]), 0)
            return (cost, 1 if from_discard else 0)
        return max(inks, key=ink_key)

    # 3. favorable challenges: kill the defender and either survive or trade up
    from . import abilities
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
