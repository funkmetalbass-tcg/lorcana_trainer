"""Phase 3 regression tests: offline parser + schema-auto runtime pipeline.

Run: python3 test_phase3.py
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lorcana.schema as schema
schema._REGISTRY = None  # ensure we read the on-disk auto+manual files

from lorcana.cards import CardDB, parse_decklist
from lorcana.engine import Game, CharInPlay
from lorcana.abilities import HAND_IMPLEMENTED
from tools.parse_abilities import parse_card, core_prose

db = CardDB("master_legal_cardlist.json")
dA, _, _ = parse_decklist("deckA.txt", db)
dB, _, _ = parse_decklist("deckB.txt", db)
C = lambda n: db.get(n)
P, F = 0, []


def ck(name, cond):
    global P
    if cond:
        P += 1
    else:
        F.append(name)


def fresh():
    g = Game(dA, dB, seed=1); g.turn = 10; return g


def put(g, n, o, ex=False, turn=1):
    ch = CharInPlay(g.next_uid(), C(n), o, turn, ex); g.chars[ch.uid] = ch
    return ch


# ---- parser unit tests: tight templates, conservative failure ----
raw_gain = {"CardType": "Character", "Cost Ink": "2", "Strength": "1",
            "Willpower": "1", "Lore Value": "1",
            "Description": "When you play this character, gain 2 lore."}
ents = parse_card("Test Gainer", raw_gain)
ck("parser: gain_lore parsed", ents and ents[0].get("effect", {}).get("type") == "gain_lore"
   and ents[0]["effect"]["amount"] == 2)

raw_compound = {"CardType": "Character", "Cost Ink": "2", "Strength": "1",
                "Willpower": "1", "Lore Value": "1",
                "Description": "When you play this character, gain 2 lore and draw a card."}
ents = parse_card("Test Compound", raw_compound)
ck("parser: compound stays unimplemented",
   ents and ents[0].get("impl") == "unimplemented")

raw_vanilla = {"CardType": "Character", "Cost Ink": "2", "Strength": "3",
               "Willpower": "3", "Lore Value": "1", "Description": ""}
ck("parser: vanilla -> no entry", parse_card("Test Vanilla", raw_vanilla) is None)

raw_kw = {"CardType": "Character", "Cost Ink": "2", "Strength": "2",
          "Willpower": "3", "Lore Value": "1",
          "Description": "Bodyguard <em>(reminder)</em>"}
ck("parser: keyword-only -> no entry", parse_card("Test KW", raw_kw) is None)

# ---- no-overlap invariant: parser never emits for a python-implemented card ----
auto_path = os.path.join("lorcana", "abilities_auto.json")
overlap = []
if os.path.exists(auto_path):
    auto = json.load(open(auto_path))
    for name in auto:
        if name in HAND_IMPLEMENTED:
            # allowed only if it's an unimplemented marker (no runtime effect)
            e = auto[name][0]
            if e.get("trigger"):
                overlap.append(name)
ck("no python/auto overlap with active triggers", not overlap)

# ---- runtime: parsed cards actually fire through schema ----
# Yzma: each opponent loses 1 lore on play
g = fresh(); g.active = 0; g.players[1].lore = 5
g.players[0].hand = [C("Yzma - Choosy Customer")]
g.players[0].ink_total = g.players[0].ink_ready = 9
plays = [a for a in g.legal_actions() if a[0] == "play" and "Yzma" in a[1]]
if plays:
    g.apply(plays[0])
    ck("runtime: Yzma opponent -1 lore", g.players[1].lore == 4)
else:
    ck("runtime: Yzma playable", False)

# Rapunzel Creative Captor: chosen opposing -3 str this turn
g = fresh(); g.active = 0
victim = put(g, "Buzz Lightyear - Space Ranger", 1, turn=1)
g.players[0].hand = [C("Rapunzel - Creative Captor")]
g.players[0].ink_total = g.players[0].ink_ready = 9
base = g.eff_strength(victim)
plays = [a for a in g.legal_actions() if a[0] == "play" and "Rapunzel - Creative" in a[1]]
if plays:
    g.apply(plays[0])
    ck("runtime: Rapunzel -3 str", g.eff_strength(victim) == base - 3)
else:
    ck("runtime: Rapunzel playable", False)

# Conditional static: Dale +1 lore only while a Chip is in play
g = fresh(); g.active = 0
dale = put(g, "Dale - Excited Friend", 0, turn=1)
ck("runtime: Dale no bonus without Chip", g.eff_lore(dale) == C("Dale - Excited Friend").lore)
chips = [n for n in db.cards if n.startswith("Chip -")]
if chips:
    put(g, chips[0], 0, turn=1)
    ck("runtime: Dale +1 with Chip", g.eff_lore(dale) == C("Dale - Excited Friend").lore + 1)

# ---- determinism: re-parsing produces identical output ----
from tools.parse_abilities import build
o1, _ = build("master_legal_cardlist.json", os.path.join("lorcana", "abilities_manual.json"))
o2, _ = build("master_legal_cardlist.json", os.path.join("lorcana", "abilities_manual.json"))
ck("parser deterministic", json.dumps(o1, sort_keys=True) == json.dumps(o2, sort_keys=True))

print(f"PASS {P}  FAIL {len(F)}")
for f in F:
    print("  FAIL:", f)
