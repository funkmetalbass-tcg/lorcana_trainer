"""Forced-scenario tests for POLICY QUALITY.

test_mechanics.py asks "does the rule fire?". This file asks "does the AI take
the line?" -- a different question, and the one that decides whether deckbuild
fitness means anything. A rules engine that resolves shift perfectly is still
useless as an evaluator if the pilot inks the shift target on turn 2.

Conventions
-----------
* `greedy_policy` is always called with epsilon=0.0. The default epsilon makes
  the policy stochastic; a flaky policy test is worse than no policy test.
* Tests are tagged [regression] (behavior that must not break) or [combo]
  (behavior added by the combos.py work). A [combo] test is expected to FAIL
  against an unpatched policies.py -- that is the point of it.
* Card choices are grounded in the master JSON, not invented:
    Woody - Waiting for a Friend   cost 1, inkable, 2/2, 1 lore
    Woody - Leader of the Toys     cost 4, inkable, 3/4, 1 lore
    Woody - Jungle Guide           cost 5, NOT inkable, 1/5, 2 lore, Shift 3
    Rex - Protective Dinosaur      cost 2, inkable, 3/1, 1 lore
    Elsa - Concerned Sister        cost 3, inkable, 2/2, 2 lore
    Virana - Fang Chief            cost 5, inkable, vanilla (no shift)
    Pumbaa - Winter Warthog        cost 6, NOT inkable, no shift
"""
import random

from lorcana.cards import CardDB, parse_decklist
from lorcana.engine import Game, CharInPlay
from lorcana.policies import greedy_policy, default_mulligan
from lorcana import mcts

db = CardDB("master_legal_cardlist.json")
deckA, _, _ = parse_decklist("deckA.txt", db)
deckB, _, _ = parse_decklist("deckB.txt", db)
C = lambda n: db.get(n)
RNG = random.Random(0)
PASS, FAIL = 0, []


def check(name, cond, info=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {info}")


def fresh():
    g = Game(deckA, deckB, seed=1)
    g.turn = 10
    return g


def put(g, name, owner, exerted=False, turn=1):
    ch = CharInPlay(g.next_uid(), C(name), owner, turn, exerted)
    g.chars[ch.uid] = ch
    return ch


def act(g):
    """The greedy policy's deterministic choice for the active player."""
    return greedy_policy(g, RNG, epsilon=0.0)


# =====================================================================
# 1. Discount units (mcts.py)
# =====================================================================

# 1a. [regression] structural guard: the terminal backup in search() must not
#     discount by tree depth. This is the exact bug the fix removes, and it is
#     easy to reintroduce while refactoring, so assert on the source.
import inspect
_src = inspect.getsource(mcts.search).replace(" ", "")
check("no per-edge discounting in search()",
      "DISCOUNT**len(path)" not in _src)
check("terminal backup discounts by elapsed turns",
      "g.turn-root_turn" in _src)

# 1b. _discount is a shared helper, so rollout terminals and in-tree terminals
#     cannot drift apart in units again. Sanity-check its shape.
check("_discount is neutral at 0 plies", mcts._discount(1.0, 0) == 1.0)
check("_discount pulls toward 0.5, never past it",
      0.5 < mcts._discount(1.0, 40) < 1.0)
check("_discount is symmetric about 0.5",
      abs((mcts._discount(1.0, 7) - 0.5) + (mcts._discount(0.0, 7) - 0.5)) < 1e-9)
check("_discount is monotone in plies",
      mcts._discount(1.0, 2) > mcts._discount(1.0, 3) > mcts._discount(1.0, 20))
# A 20-half-turn game is ordinary, not a stall. Under the old per-edge scheme
# the same game could be discounted as if ~100+ plies had passed, which is what
# crushed deep combo lines. Guard the magnitude.
check("a normal-length win keeps most of its value",
      mcts._discount(1.0, 20) > 0.75,
      f"(got {mcts._discount(1.0, 20):.3f})")

# 1c. [regression] behavioral: with lethal available, MCTS takes it now.
g = fresh(); g.active = 0
g.players[0].lore = 19
w = put(g, "Woody - Waiting for a Friend", 0, turn=1)   # 1 lore -> exactly 20
best, ranked = mcts.search(g, iterations=120, rng=random.Random(3), perspective=0)
check("MCTS takes immediate lethal", best[0] == "quest" and best[1] == w.uid,
      f"(chose {best})")


# =====================================================================
# 2. Ink selection (combo protection)
# =====================================================================

# 2a. [regression] with no combo in hand, still ink the most expensive inkable.
g = fresh(); g.active = 0
g.players[0].hand = [C("Woody - Leader of the Toys"), C("Rex - Protective Dinosaur")]
a = act(g)
check("baseline: inks the highest-cost inkable",
      a[0] == "ink" and a[1] == "Woody - Leader of the Toys", f"(chose {a})")

# 2b. [combo] a shift card in hand makes its base a bad ink, even though the
#     base is the most expensive inkable card available.
g = fresh(); g.active = 0
g.players[0].hand = [C("Woody - Leader of the Toys"),      # 4, inkable, the base
                     C("Rex - Protective Dinosaur"),       # 2, inkable, filler
                     C("Woody - Jungle Guide")]            # 5, NOT inkable, Shift 3
a = act(g)
check("does not ink a shift target while holding the shift",
      a[0] == "ink" and a[1] == "Rex - Protective Dinosaur", f"(chose {a})")

# 2c. [combo] a shift card with no base anywhere is NOT protected -- it is a
#     fine ink, and treating it as sacred would strand the policy on ink.
g = fresh(); g.active = 0
g.players[0].hand = [C("Rex - Protective Dinosaur"),
                     C("Mr. Incredible - Super Strong")]   # 5, inkable, Shift 3, no base
a = act(g)
check("orphan shift card is still inkable",
      a[0] == "ink" and a[1] == "Mr. Incredible - Super Strong", f"(chose {a})")

# 2d. [combo] protection also applies when the base is already on the board.
g = fresh(); g.active = 0
put(g, "Woody - Waiting for a Friend", 0, turn=1)
g.players[0].hand = [C("Woody - Jungle Guide"),            # not inkable anyway
                     C("Virana - Fang Chief")]             # 5, inkable, vanilla
a = act(g)
check("board base + shift in hand: inks the unrelated card",
      a[0] == "ink" and a[1] == "Virana - Fang Chief", f"(chose {a})")


# 2e. [combo] if every inkable card is protected the policy must still ink --
#     protection is a sort key, not a veto, or greedy stalls on ink entirely.
g = fresh(); g.active = 0
g.players[0].hand = [C("Woody - Leader of the Toys"), C("Woody - Waiting for a Friend"),
                     C("Woody - Jungle Guide")]
a = act(g)
check("all inkable cards protected: still inks", a[0] == "ink", f"(chose {a})")


# =====================================================================
# 3. Shift priority
# =====================================================================

# 3a. [combo] an affordable shift is taken. Hand holds only the (non-inkable)
#     shift card, so no ink action competes for the turn.
g = fresh(); g.active = 0
base = put(g, "Woody - Waiting for a Friend", 0, turn=1)
g.players[0].hand = [C("Woody - Jungle Guide")]
g.players[0].ink_total = g.players[0].ink_ready = 3
g.players[0].deck = list(deckA[:10])
a = act(g)
check("takes an available shift over questing the base",
      a[0] == "play" and a[1] == "Woody - Jungle Guide"
      and dict(a[2]).get("shift") == base.uid, f"(chose {a})")

# 3a-bis. [combo] the discriminating version: a shift competing against a
#     MORE EXPENSIVE non-shift play. Section 4 of greedy ranks plays by printed
#     cost, and a shift card's printed cost is the undiscounted one -- so
#     without the 2b tier the 6-drop wins and the shift is deferred. Both cards
#     are non-inkable so no ink action fires first.
g = fresh(); g.active = 0
base = put(g, "Woody - Waiting for a Friend", 0, turn=1)
g.players[0].hand = [C("Woody - Jungle Guide"),        # 5, Shift 3, not inkable
                     C("Pumbaa - Winter Warthog")]     # 6, not inkable, no shift
g.players[0].ink_total = g.players[0].ink_ready = 6
g.players[0].deck = list(deckA[:10])
a = act(g)
check("shift beats a pricier non-shift play",
      a[0] == "play" and a[1] == "Woody - Jungle Guide", f"(chose {a})")

# 3c. [regression] the shift tier must not hijack ordinary plays.
g = fresh(); g.active = 0
put(g, "Woody - Waiting for a Friend", 0, turn=1)
g.players[0].hand = [C("Pumbaa - Winter Warthog")]
g.players[0].ink_total = g.players[0].ink_ready = 6
g.players[0].deck = list(deckA[:10])
a = act(g)
check("non-shift play is unaffected",
      a[0] == "play" and a[1] == "Pumbaa - Winter Warthog", f"(chose {a})")

# 3b. [regression] shift priority must not fire when the shift is unaffordable.
g = fresh(); g.active = 0
base = put(g, "Woody - Waiting for a Friend", 0, turn=1)
g.players[0].hand = [C("Woody - Jungle Guide")]
g.players[0].ink_total = g.players[0].ink_ready = 0
g.players[0].deck = list(deckA[:10])
a = act(g)
check("no shift offered with no ink: falls through to questing",
      a[0] != "play", f"(chose {a})")


# =====================================================================
# 4. Challenge scoring (don't eat your own combo piece)
# =====================================================================

# Attacker Woody - Waiting for a Friend (2/2, 1 lore) vs exerted
# Elsa - Concerned Sister (2/2, 2 lore): mutual kill, defender worth more lore.
# Baseline greedy scores this a 3 ("trade up") and takes it.

# 4a. [regression] with no shift card in hand, the trade is still taken.
g = fresh(); g.active = 0
att = put(g, "Woody - Waiting for a Friend", 0, turn=1)
dfn = put(g, "Elsa - Concerned Sister", 1, True, turn=1)
g.players[0].hand = []
g.players[0].ink_total = g.players[0].ink_ready = 0
a = act(g)
check("baseline: trades up into a higher-lore defender",
      a[0] == "challenge" and a[1] == att.uid, f"(chose {a})")

# 4b. [combo] same board, but the attacker is a live shift base. Ink is 0 so
#     the shift itself is unaffordable -- this isolates the challenge penalty
#     from the shift-priority tier above.
g = fresh(); g.active = 0
att = put(g, "Woody - Waiting for a Friend", 0, turn=1)
dfn = put(g, "Elsa - Concerned Sister", 1, True, turn=1)
g.players[0].hand = [C("Woody - Jungle Guide")]
g.players[0].ink_total = g.players[0].ink_ready = 0
a = act(g)
check("won't trade away a character a held shift card wants",
      not (a[0] == "challenge" and a[1] == att.uid), f"(chose {a})")

# 4c. [combo] the penalty is a discount, not a prohibition: a free kill
#     (defender dies, attacker lives) is still worth taking.
g = fresh(); g.active = 0
att = put(g, "Woody - Leader of the Toys", 0, turn=1)      # 3/4
dfn = put(g, "Rex - Protective Dinosaur", 1, True, turn=1)  # 3/1 -> dies, deals 3 < 4
g.players[0].hand = [C("Woody - Jungle Guide")]
g.players[0].ink_total = g.players[0].ink_ready = 0
a = act(g)
check("still takes a free kill with a shift base",
      a[0] == "challenge" and a[1] == att.uid, f"(chose {a})")


# =====================================================================
# 5. Mulligan
# =====================================================================

# 5a. [regression] expensive cards with no combo attached are still bottomed.
hand = [C("Virana - Fang Chief"), C("Rex - Protective Dinosaur")]
g = fresh(); g.players[0].hand = list(hand)
out = default_mulligan(g, 0)
check("baseline: bottoms an unrelated 5-drop",
      [c.name for c in out] == ["Virana - Fang Chief"],
      f"(bottomed {[c.name for c in out]})")

# 5b. [combo] a shift card is kept when its base is in the same opening hand,
#     while an unrelated 5-drop in the same hand is still bottomed.
hand = [C("Woody - Jungle Guide"),          # 5, would be bottomed by cost rule
        C("Woody - Leader of the Toys"),    # 4, its base
        C("Virana - Fang Chief")]           # 5, unrelated
g = fresh(); g.players[0].hand = list(hand)
out = [c.name for c in default_mulligan(g, 0)]
check("keeps a live shift card in the opener", "Woody - Jungle Guide" not in out,
      f"(bottomed {out})")
check("still bottoms the unrelated 5-drop", "Virana - Fang Chief" in out,
      f"(bottomed {out})")

# 5c. [combo] a shift card with no base in hand is bottomed as before.
hand = [C("Woody - Jungle Guide"), C("Rex - Protective Dinosaur")]
g = fresh(); g.players[0].hand = list(hand)
out = [c.name for c in default_mulligan(g, 0)]
check("bottoms an orphan shift card", "Woody - Jungle Guide" in out,
      f"(bottomed {out})")


# =====================================================================
print(f"\n{PASS} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAIL:", f)
raise SystemExit(1 if FAIL else 0)
