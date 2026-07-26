"""Gauntlet checkpoint/resume regression test. Run: python3 test_checkpoint.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lorcana import gauntlet as G

P, F = 0, []
def ck(name, cond):
    global P
    if cond: P += 1
    else: F.append(name)

CANDS = ["Winterspell", "John Silver - Greedy Treasure Seeker"]
KW = dict(games=8, iters=1, a_pol="greedy", b_pol="greedy", workers=1, seed0=0)

# reference: uninterrupted, no checkpoint
ref = G.run_gauntlet("master_legal_cardlist.json", "deckA.txt", ["deckB.txt"],
                     CANDS, checkpoint=None, **KW)

# crash midway, then resume from checkpoint
ckpath = "/tmp/_test_ckpt.jsonl"
if os.path.exists(ckpath):
    os.remove(ckpath)

orig = G._play
state = {"n": 0}
def crashing(*a, **k):
    state["n"] += 1
    if state["n"] > 9:
        raise RuntimeError("simulated crash")
    return orig(*a, **k)

G._play = crashing
crashed = False
try:
    G.run_gauntlet("master_legal_cardlist.json", "deckA.txt", ["deckB.txt"],
                   CANDS, checkpoint=ckpath, **KW)
except RuntimeError:
    crashed = True
G._play = orig
ck("crash interrupted the run", crashed)
ck("checkpoint file was written", os.path.exists(ckpath))

resumed = G.run_gauntlet("master_legal_cardlist.json", "deckA.txt", ["deckB.txt"],
                         CANDS, checkpoint=ckpath, **KW)
ck("resumed baseline matches uninterrupted", resumed["baseline"] == ref["baseline"])
ck("resumed cuts match uninterrupted", resumed["cut"] == ref["cut"])

# signature guard: different params must not reuse the checkpoint
import io
err = io.StringIO(); old = sys.stderr; sys.stderr = err
G.run_gauntlet("master_legal_cardlist.json", "deckA.txt", ["deckB.txt"],
               CANDS, checkpoint=ckpath, **{**KW, "games": 6})
sys.stderr = old
ck("signature mismatch is detected", "does not match" in err.getvalue())

# resume=False starts fresh
G.run_gauntlet("master_legal_cardlist.json", "deckA.txt", ["deckB.txt"],
               CANDS, checkpoint=ckpath, resume=False, **KW)
ck("no-resume produced a fresh valid file", os.path.exists(ckpath))

# a fully-completed checkpoint re-run skips everything and still matches
again = G.run_gauntlet("master_legal_cardlist.json", "deckA.txt", ["deckB.txt"],
                       CANDS, checkpoint=ckpath, **KW)
ck("re-run of complete checkpoint matches", again["cut"] == ref["cut"])

# partial results are written mid-run and marked as incomplete
ppath = "/tmp/_test_partial.txt"
if os.path.exists(ppath):
    os.remove(ppath)
state2 = {"n": 0}
def crashing2(*a, **k):
    state2["n"] += 1
    if state2["n"] > 12:
        raise RuntimeError("simulated kill")
    return orig(*a, **k)
G._play = crashing2
try:
    G.run_gauntlet("master_legal_cardlist.json", "deckA.txt", ["deckB.txt"],
                   CANDS, checkpoint=None, partial_out=ppath,
                   field_labels=["deckB"], **KW)
except RuntimeError:
    pass
G._play = orig
ck("partial report written mid-run", os.path.exists(ppath))
if os.path.exists(ppath):
    body = open(ppath).read()
    ck("partial report marked PARTIAL", "PARTIAL RESULTS" in body)
    ck("partial report shows sample sizes", "n=" in body)
    os.remove(ppath)

if os.path.exists(ckpath):
    os.remove(ckpath)

print(f"PASS {P}  FAIL {len(F)}")
for f in F:
    print("  FAIL:", f)
