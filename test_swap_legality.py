"""Regression tests for cut/backfill legality (the 5th-copy bug).
Run: python3 test_swap_legality.py
"""
import os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lorcana.cards import CardDB, parse_decklist
from lorcana.analyze import apply_cut, pick_filler, MAX_COPIES
from lorcana.gauntlet import _apply_cut

P, F = 0, []
def ck(name, cond):
    global P
    if cond: P += 1
    else: F.append(name)

db = CardDB("master_legal_cardlist.json")
deck, _, _ = parse_decklist("deckA.txt", db)


def illegal(d):
    return {n: k for n, k in Counter(c.name for c in d).items() if k > MAX_COPIES}


# every card in the deck, cut one at a time, must never yield >4 copies
uniq = sorted({c.name for c in deck})
bad_analyze, bad_gauntlet, size_bad = [], [], []
for cut in uniq:
    t1, _ = apply_cut(deck, cut)
    if illegal(t1):
        bad_analyze.append((cut, illegal(t1)))
    if len(t1) != len(deck):
        size_bad.append((cut, len(t1)))
    t2 = _apply_cut(deck, cut)
    if illegal(t2):
        bad_gauntlet.append((cut, illegal(t2)))

ck("analyze.apply_cut never makes a 5th copy", not bad_analyze)
ck("gauntlet._apply_cut never makes a 5th copy", not bad_gauntlet)
ck("cut preserves deck size when a filler exists", not size_bad)

# the filler is never the cut card
t, note = apply_cut(deck, "Winterspell")
ck("filler is not the cut card", "Winterspell" not in note)

# filler prefers the card with the fewest copies
counts = Counter(c.name for c in deck)
filler, _ = pick_filler(deck, "Winterspell")
eligible = [n for n, k in counts.items() if n != "Winterspell" and k < MAX_COPIES]
if eligible and filler is not None:
    fewest = min(counts[n] for n in eligible)
    ck("filler tops up a lowest-count card", counts[filler.name] == fewest)

# determinism: same inputs -> same filler
f1, _ = pick_filler(deck, "Winterspell")
f2, _ = pick_filler(deck, "Winterspell")
ck("filler choice is deterministic", f1.name == f2.name)

# edge case: every other card already at 4 copies -> no filler, 59-card deck
seen, uniq_cards = set(), []
for c in deck:
    if c.name not in seen:
        seen.add(c.name); uniq_cards.append(c)
allfour = []
for c in uniq_cards[:15]:
    allfour.extend([c] * 4)
ck("test fixture is a legal 60", len(allfour) == 60 and not illegal(allfour))
t, note = apply_cut(allfour, uniq_cards[0].name)
ck("no-legal-filler yields 59 cards", len(t) == 59)
ck("no-legal-filler is still legal", not illegal(t))
ck("no-legal-filler is reported in the note", "no legal filler" in note)

# cutting a card not in the deck is a no-op
t, note = apply_cut(deck, "This Card Does Not Exist")
ck("cutting an absent card is a no-op", len(t) == len(deck))

print(f"PASS {P}  FAIL {len(F)}")
for f in F:
    print("  FAIL:", f)
