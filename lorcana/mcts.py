"""Single-observer Information Set MCTS (ISMCTS) with determinization.

Each iteration: clone the real state, shuffle everything the deciding player
cannot see (opponent hand+deck as one pool, own deck), then run UCT selection
over a tree whose edges are actions. Rollouts use the greedy policy.
"""
import math, random
from .policies import greedy_policy

ROLLOUT_TURN_CAP = 50   # half-turns beyond current before heuristic eval
UCB_C = 0.9
DISCOUNT = 0.97         # per-ply value decay toward neutral (rewards faster wins)


class Node:
    __slots__ = ("children", "n", "player")

    def __init__(self, player):
        self.children = {}   # action -> [visits, total_value, availability, Node]
        self.n = 0
        self.player = player


def determinize(game, viewer, rng):
    g = game.clone()
    opp = 1 - viewer
    po = g.players[opp]
    pool = po.hand + po.deck
    rng.shuffle(pool)
    hand_n = len(po.hand)
    po.hand = pool[:hand_n]
    po.deck = pool[hand_n:]
    rng.shuffle(g.players[viewer].deck)
    return g


def evaluate(game, perspective):
    """Terminal or heuristic value in [0,1] for `perspective`."""
    if game.winner is not None:
        return 1.0 if game.winner == perspective else 0.0
    me, op = game.players[perspective], game.players[1 - perspective]
    score = 3.0 * (me.lore - op.lore)
    for ch in game.chars.values():
        v = 2.0 * game.eff_lore(ch) + 0.5 * (game.eff_strength(ch) + game.eff_willpower(ch) - ch.damage)
        score += v if ch.owner == perspective else -v
    for loc in game.locs.values():
        v = 3.0 * game.loc_lore(loc) + 0.3 * (loc.card.willpower - loc.damage)
        score += v if loc.owner == perspective else -v
    score += 0.4 * (len(me.hand) - len(op.hand))
    score += 0.3 * (me.ink_total - op.ink_total)
    return 1.0 / (1.0 + math.exp(-score / 12.0))


def rollout(game, perspective, rng):
    """Play out with the greedy policy; discount the result slightly per turn
    elapsed so that faster wins (and slower losses) are preferred. This lets the
    search distinguish a lethal line from one that merely wins eventually."""
    start_turn = game.turn
    cap_turn = game.turn + ROLLOUT_TURN_CAP
    while game.winner is None and game.turn < cap_turn:
        a = greedy_policy(game, rng, epsilon=0.15)
        game.apply(a)
    v = evaluate(game, perspective)
    plies = game.turn - start_turn
    # pull value toward 0.5 (neutral) as the game drags on: a win in 0 plies
    # keeps its full value; each ply bleeds a little certainty away.
    discount = DISCOUNT ** plies
    return 0.5 + (v - 0.5) * discount


def search(game, iterations=400, rng=None, perspective=None):
    """Returns (best_action, ranked list of (action, visits, mean value))."""
    rng = rng or random.Random()
    viewer = perspective if perspective is not None else game.active
    root = Node(game.active)

    for _ in range(iterations):
        g = determinize(game, viewer, rng)
        node = root
        path = []
        # selection / expansion
        while g.winner is None:
            legal = g.legal_actions()
            for a in legal:
                if a not in node.children:
                    node.children[a] = [0, 0.0, 0, None]
            for a in legal:
                node.children[a][2] += 1  # availability
            untried = [a for a in legal if node.children[a][0] == 0]
            if untried:
                a = rng.choice(untried)
                path.append((node, a))
                g.apply(a)
                entry = node.children[a]
                if entry[3] is None:
                    entry[3] = Node(g.active)
                break
            # UCB over currently legal actions
            best, best_v = None, -1
            for a in legal:
                vis, tot, avail, child = node.children[a]
                q = tot / vis
                if node.player != viewer:
                    q = 1.0 - q
                u = q + UCB_C * math.sqrt(math.log(max(2, avail)) / vis)
                if u > best_v:
                    best, best_v = a, u
            path.append((node, best))
            g.apply(best)
            nxt = node.children[best][3]
            if nxt is None:
                node.children[best][3] = Node(g.active)
                break
            node = nxt
        # rollout + backprop (values stored from viewer's perspective).
        # If selection reached a terminal state, discount by the plies descended
        # so an immediate win outranks a deferred one, consistent with rollout.
        if g.winner is None:
            val = rollout(g, viewer, rng)
        else:
            raw = evaluate(g, viewer)
            val = 0.5 + (raw - 0.5) * (DISCOUNT ** len(path))
        for node_, a_ in path:
            e = node_.children[a_]
            e[0] += 1
            e[1] += val
            node_.n += 1

    ranked = sorted(
        [(a, e[0], (e[1] / e[0]) if e[0] else 0.0) for a, e in root.children.items()],
        key=lambda t: (-t[1], -t[2]))
    best = ranked[0][0] if ranked else ("pass",)
    return best, ranked


def mcts_policy_factory(iterations=300, seed=None):
    rng = random.Random(seed)
    def policy(game, _rng=None):
        best, _ = search(game, iterations=iterations, rng=rng)
        return best
    return policy
