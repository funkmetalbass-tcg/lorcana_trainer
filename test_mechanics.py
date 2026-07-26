"""Forced-scenario tests for complex mechanics."""
from lorcana.cards import CardDB, parse_decklist
from lorcana.engine import Game, CharInPlay, LocInPlay

db = CardDB("master_legal_cardlist.json")
deckA, _, _ = parse_decklist("deckA.txt", db)
deckB, _, _ = parse_decklist("deckB.txt", db)
C = lambda n: db.get(n)
PASS, FAIL = 0, []

def check(name, cond, info=""):
    global PASS
    if cond: PASS += 1
    else: FAIL.append(f"{name} {info}")

def fresh():
    g = Game(deckA, deckB, seed=1)
    g.turn = 10
    return g

def put(g, name, owner, exerted=False, turn=1):
    ch = CharInPlay(g.next_uid(), C(name), owner, turn, exerted)
    g.chars[ch.uid] = ch
    return ch

def put_loc(g, name, owner):
    l = LocInPlay(g.next_uid(), C(name), owner)
    g.locs[l.uid] = l
    return l

# 1. Sing Together + Under the Sea
g = fresh(); g.active = 0
a1 = put(g, "Woody & Buzz Lightyear - Best Buddies", 0)   # cost 7
a2 = put(g, "Aurora - Holding Court", 0)                  # cost 1
e1 = put(g, "Tinker Bell - Fancy Footwork", 1)            # 3/1 -> str 3 stays
e2 = put(g, "Beast - Snowfield Troublemaker", 1)          # 3/1
e3 = put(g, "Elsa - Concerned Sister", 1)                 # 2/2 -> bottomed
g.players[0].hand.append(C("Under the Sea"))
acts = g.legal_actions()
st = [a for a in acts if a[0] == "sing_together"]
check("sing_together available", len(st) == 1)
g.apply(st[0])
check("UtS bottoms str<=2 only", e3.uid not in g.chars and e1.uid in g.chars and e2.uid in g.chars)
check("UtS singers exerted", a1.exerted and a2.exerted)
check("UtS card in discard", C("Under the Sea") in g.players[0].discard)

# 2. Shift Woody Jungle Guide onto exerted damaged Woody; quest trigger
g = fresh(); g.active = 0
base = put(g, "Woody - Waiting for a Friend", 0, exerted=False, turn=1)
base.damage = 1
g.players[0].hand.append(C("Woody - Jungle Guide"))
g.players[0].hand.append(C("Rex - Protective Dinosaur"))  # cost 2, free-play target
g.players[0].ink_total = g.players[0].ink_ready = 3
g.players[0].deck = list(deckA[:10])
shift_acts = [a for a in g.legal_actions() if a[0] == "play" and a[1].endswith("Jungle Guide")
              and dict(a[2]).get("shift")]
check("shift action offered", len(shift_acts) == 1)
g.apply(shift_acts[0])
check("shift keeps damage & uid", base.card.name == "Woody - Jungle Guide" and base.damage == 1)
check("shift keeps dry state (can quest)", any(a[0] == "quest" and a[1] == base.uid for a in g.legal_actions()))
h0 = len(g.players[0].hand)
g.apply(("quest", base.uid))
check("JG quest: draw + free Rex", len(g.players[0].hand) == h0 and  # +1 draw -1 free-played
      any(c.card.name == "Rex - Protective Dinosaur" for c in g.my_chars(0)))
check("JG willpower buff on other toys",
      g.eff_willpower([c for c in g.my_chars(0) if c.card.base_name == "Rex"][0]) == 2)

# 3. Sleepy Hollow: quest there with sh_banish -> banish loc, +2 lore, evasive
g = fresh(); g.active = 1
sh = put_loc(g, "Sleepy Hollow - The Bridge", 1)
q = put(g, "Elsa - Concerned Sister", 1, turn=1); q.location = sh.uid
att = put(g, "Buzz Lightyear - Space Ranger", 0, turn=1)
# both choices should be offered
qopts = [a for a in g.legal_actions() if a[0] == "quest" and a[1] == q.uid]
check("Sleepy Hollow offers keep+banish", len(qopts) == 2 and
      {a[2] for a in qopts} == {"sh_keep", "sh_banish"})
g.apply(("quest", q.uid, "sh_banish"))
check("Sleepy Hollow banished, +2+2 lore", sh.uid not in g.locs and g.players[1].lore == 4)
g.active = 0
tgts = g.challenge_targets(att)
check("evasive grant blocks challenge", ("char", q.uid) not in tgts)

# 3b. Sleepy Hollow with sh_keep -> location stays, only quest lore
g = fresh(); g.active = 1
sh2 = put_loc(g, "Sleepy Hollow - The Bridge", 1)
q2 = put(g, "Elsa - Concerned Sister", 1, turn=1); q2.location = sh2.uid
g.apply(("quest", q2.uid, "sh_keep"))
check("sh_keep leaves location & no bonus lore", sh2.uid in g.locs and g.players[1].lore == 2)

# 4. Jack-Jack mill: location on top -> banish opposing char
g = fresh(); g.active = 1
jj = put(g, "Jack-Jack Parr - Incredible Potential", 1, turn=1)
victim = put(g, "The Queen - Devious Disguise", 0, turn=1)
g.players[1].deck = [C("Castle Wyvern - Above the Clouds")]
g.players[1].hand = []
from lorcana import abilities
abilities.start_of_turn(g, 1)
check("Jack-Jack location mill banishes", victim.uid not in g.chars)

# 5. Mickey enters exerted; SECRET PATH debuff on other quest
g = fresh(); g.active = 0
g.players[0].hand.append(C("Mickey Mouse - Expedition Leader"))
g.players[0].ink_total = g.players[0].ink_ready = 4
questy = put(g, "Buzz Lightyear - Space Ranger", 0, turn=1)
tgt = put(g, "John Silver - Greedy Treasure Seeker", 1, turn=1)
ex = [a for a in g.legal_actions() if a[0] == "play" and "Mickey" in a[1] and dict(a[2]).get("exerted")]
check("Mickey exerted-entry option", len(ex) == 1)
g.apply(ex[0])
mick = [c for c in g.my_chars(0) if c.card.base_name == "Mickey Mouse"][0]
check("Mickey entered exerted", mick.exerted)
g.apply(("quest", questy.uid))
check("SECRET PATH -2 str", g.eff_strength(tgt) == 1)
g.active = 1; g.begin_turn()  # start of P1 turn: debuff persists (until P0's next)
check("debuff persists on opp turn", g.eff_strength(tgt) == 1)
g.active = 0; g.begin_turn()
check("debuff expires at P0 turn start", g.eff_strength(tgt) == 3)

# 6. Discount stacking: Willow + Aurora (Queen is a 'Queen')
g = fresh(); g.active = 0
put(g, "Grandmother Willow - Ancient Advisor", 0, turn=1)
aur = put(g, "Aurora - Holding Court", 0, turn=1)
g.apply(("quest", aur.uid))
q = C("The Queen - Devious Disguise")
check("Willow+Aurora discount", g.play_cost(0, q) == 2, g.play_cost(0, q))
b = C("Buzz Lightyear - Space Ranger")
check("Willow only for non-royal", g.play_cost(0, b) == 1)
g.players[0].hand.append(C("Buzz Lightyear - Space Ranger"))
g.players[0].ink_total = g.players[0].ink_ready = 1
g.apply([a for a in g.legal_actions() if a[0] == "play" and "Buzz" in a[1]][0])
check("Willow consumed once", g.play_cost(0, q) == 3, g.play_cost(0, q))

# 7. Bodyguard restriction + RUN AWAY! on opponent's turn
g = fresh(); g.active = 1
rex = put(g, "Rex - Protective Dinosaur", 0, exerted=True, turn=1)
soft = put(g, "Lenny - Toy Binoculars", 0, exerted=True, turn=1)
att = put(g, "Beast - Snowfield Troublemaker", 1, turn=1)
tg = g.challenge_targets(att)
check("bodyguard restricts", ("char", soft.uid) not in tg and ("char", rex.uid) in tg)
g.apply(("challenge", att.uid, "char", rex.uid))
check("Rex banished, RUN AWAY lore", rex.uid not in g.chars and g.players[0].lore == 1)

# 8. Alien return during your turn
g = fresh(); g.active = 0
al = put(g, "Alien - True Believer", 0, turn=1)
g.players[0].discard.append(C("Alien - True Believer"))
g.banish_char(al)
check("Alien returns another Alien", any(c.name == "Alien - True Believer" for c in g.players[0].hand))

# 9. CHEAP SHOT at Nomanisan
g = fresh(); g.active = 1
nom = put_loc(g, "The Island of Nomanisan - Syndrome's Headquarters", 1)
killer = put(g, "Jack-Jack Parr - Incredible Potential", 1, turn=1); killer.location = nom.uid
prey = put(g, "Aurora - Holding Court", 0, exerted=True, turn=1)         # 1/2 dies
bystander = put(g, "Lenny - Toy Binoculars", 0, turn=1)                   # 0/2, cheap-shot kill
g.apply(("challenge", killer.uid, "char", prey.uid))
check("CHEAP SHOT fires", bystander.uid not in g.chars or bystander.damage == 2)

# 10. Zootopia move trigger: zoo_draw draws+discards; zoo_skip does neither
g = fresh(); g.active = 1
zoo = put_loc(g, "Zootopia - Police Headquarters", 1)
mover = put(g, "Beast - Snowfield Troublemaker", 1, turn=1)
other = put(g, "Tinker Bell - Fancy Footwork", 1, turn=1)
g.players[1].deck = list(deckB[:10]); g.players[1].hand = [C("Winterspell")]
g.players[1].ink_total = g.players[1].ink_ready = 5
mopts = [a for a in g.legal_actions() if a[0] == "move" and a[1] == mover.uid and a[2] == zoo.uid]
check("Zootopia offers draw+skip", len(mopts) == 2 and
      {a[3] for a in mopts} == {"zoo_draw", "zoo_skip"})
g.apply(("move", mover.uid, zoo.uid, "zoo_draw"))
check("Zootopia draw+discard nets 1-in-1-out", len(g.players[1].hand) == 1 and len(g.players[1].discard) == 1)
n_disc = len(g.players[1].discard)
g.apply(("move", other.uid, zoo.uid, "zoo_draw"))
check("Zootopia once per turn", len(g.players[1].discard) == n_disc)
# zoo_skip variant
g = fresh(); g.active = 1
zoo3 = put_loc(g, "Zootopia - Police Headquarters", 1)
m3 = put(g, "Beast - Snowfield Troublemaker", 1, turn=1)
g.players[1].deck = list(deckB[:5]); g.players[1].hand = [C("Winterspell")]
g.players[1].ink_total = g.players[1].ink_ready = 5
g.apply(("move", m3.uid, zoo3.uid, "zoo_skip"))
check("zoo_skip: no draw, no discard", len(g.players[1].hand) == 1 and len(g.players[1].discard) == 0)

# 12. Launchpad STAND GUARD: locations get Resist +1
g = fresh(); g.active = 0
lp = put(g, "Launchpad - Hideout Defender", 1)
myloc = put_loc(g, "Castle Wyvern - Above the Clouds", 1)
from lorcana import abilities as _ab
check("Launchpad grants loc resist", _ab.location_resist(g, myloc) == 1)
atk = put(g, "Beast - Snowfield Troublemaker", 0, turn=1)  # 3 str
g.apply(("challenge", atk.uid, "loc", myloc.uid))
check("location took reduced dmg (3-1=2)", myloc.damage == 2)

# 13. Carl ADVENTURE AWAITS: quest at location draws = loc lore
g = fresh(); g.active = 1
fat = put_loc(g, "Fat Cat's Club - Seedy Headquarters", 1)  # lore 2
carl = put(g, "Carl Fredricksen - On the Move", 1, turn=1); carl.location = fat.uid
g.players[1].deck = list(deckA[:6])
h0 = len(g.players[1].hand)
g.apply(("quest", carl.uid))
check("Carl draws = loc lore (2)", len(g.players[1].hand) == h0 + 2)

# 13b. Carl MOVING PARTNER: playing a location moves Carl + 1 other there free
g = fresh(); g.active = 1
carl2 = put(g, "Carl Fredricksen - On the Move", 1, turn=1)
buddy = put(g, "John Silver - Greedy Treasure Seeker", 1, turn=1)
g.players[1].hand.append(C("Zootopia - Police Headquarters"))
g.players[1].deck = list(deckA[:6])
g.players[1].ink_total = g.players[1].ink_ready = 5
play_zoo = [a for a in g.legal_actions() if a[0] == "play" and "Zootopia" in a[1]][0]
g.apply(play_zoo)
newloc = [l for l in g.my_locs(1) if l.card.base_name == "Zootopia"][0]
check("MOVING PARTNER moved Carl", carl2.location == newloc.uid)
check("MOVING PARTNER moved a buddy", buddy.location == newloc.uid)

# 14. Pocahontas WANDERING SPIRIT: needs ANOTHER char played this turn
g = fresh(); g.active = 1
g.players[1].discard.append(C("Sleepy Hollow - The Bridge"))
poca = put(g, "Pocahontas - Steadfast Traveler", 1, turn=1)
# quest with no other char played this turn -> no return
g.apply(("quest", poca.uid))
check("Pocahontas no-op without another char played",
      not any(c.base_name == "Sleepy Hollow" for c in g.players[1].hand))
# now play another character this turn, then quest a second Pocahontas
g2 = fresh(); g2.active = 1
g2.players[1].discard.append(C("Sleepy Hollow - The Bridge"))
g2.players[1].hand.append(C("Gantu - Hamsterviel's Accomplice"))
g2.players[1].ink_total = g2.players[1].ink_ready = 5
poca2 = put(g2, "Pocahontas - Steadfast Traveler", 1, turn=1)
gantu_play = [a for a in g2.legal_actions() if a[0] == "play" and "Gantu" in a[1]][0]
g2.apply(gantu_play)
g2.apply(("quest", poca2.uid))
check("Pocahontas returns location after other char played",
      any(c.base_name == "Sleepy Hollow" for c in g2.players[1].hand))

# 15. Gantu EASY TARGET: discards a chosen card on play
g = fresh(); g.active = 1
g.players[1].hand = [C("Gantu - Hamsterviel's Accomplice"), C("Winterspell"), C("Touch the Sky")]
g.players[1].ink_total = g.players[1].ink_ready = 5
gopts = [a for a in g.legal_actions() if a[0] == "play" and "Gantu" in a[1]]
check("Gantu offers discard choices", len(gopts) >= 2)
disc_winter = [a for a in gopts if dict(a[2]).get("discard") == "Winterspell"][0]
g.apply(disc_winter)
check("Gantu discarded chosen card", any(c.name == "Winterspell" for c in g.players[1].discard)
      and not any(c.name == "Winterspell" for c in g.players[1].hand))
# ============ PHASE 1: generic keywords (pool cards outside both decks) ======

# 17. Ward: excluded from opponent-chosen effects but still challengeable
g = fresh(); g.active = 1
warded = put(g, "Merida - Defiant Daughter", 0, exerted=True, turn=1)      # Ward 2/3
soft = put(g, "Buzz Lightyear - Space Ranger", 0, turn=1)                   # no Ward
from lorcana import abilities as AB
tgt = AB._best_opp_char(g, 1)
check("Ward excluded from chosen effects", tgt is not None and tgt.uid == soft.uid)
att = put(g, "Beast - Snowfield Troublemaker", 1, turn=1)
check("Ward still challengeable", ("char", warded.uid) in g.challenge_targets(att))

# 18. Reckless: can't quest; pass blocked while a challenge is available
g = fresh(); g.active = 0
gaston = put(g, "Gaston - Arrogant Hunter", 0, turn=1)                      # Reckless 4/2
victim = put(g, "Elsa - Concerned Sister", 1, exerted=True, turn=1)
acts = g.legal_actions()
check("Reckless can't quest", not any(a[0] == "quest" and a[1] == gaston.uid for a in acts))
check("Reckless blocks pass when challenge available", not any(a[0] == "pass" for a in acts))
g.apply(("challenge", gaston.uid, "char", victim.uid))
check("pass returns after Reckless challenges", any(a[0] == "pass" for a in g.legal_actions()))
# no challenge targets -> pass allowed
g2 = fresh(); g2.active = 0
put(g2, "Gaston - Arrogant Hunter", 0, turn=1)
put(g2, "Elsa - Concerned Sister", 1, turn=1)  # ready, not challengeable
check("Reckless with no target allows pass", any(a[0] == "pass" for a in g2.legal_actions()))

# 19. Support: quester's strength added to best other character this turn
g = fresh(); g.active = 0
sup = put(g, "Agustin Madrigal - Exceptionally Kind", 0, turn=1)            # Support 3/6
ally = put(g, "Buzz Lightyear - Space Ranger", 0, turn=1)                   # 3/3
base_str = g.eff_strength(ally)
g.apply(("quest", sup.uid))
check("Support adds strength this turn", g.eff_strength(ally) == base_str + 3)
g.apply(("pass",))
check("Support buff expires at end of turn", g.eff_strength(ally) == base_str)

# 20. Singer: sings songs above own ink cost, up to Singer N
# ("This Growing Pressure" is a 3-cost song; its effect text is not yet
# implemented, which is fine -- this tests the singing MECHANICS only.)
g = fresh(); g.active = 0
bard = put(g, "Alan-a-Dale - Loyal Bard", 0, turn=1)                        # cost 2, Singer 4
g.players[0].hand.append(C("This Growing Pressure"))                        # song, cost 3
g.players[0].deck = list(deckB[:10])
acts = g.legal_actions()
check("Singer 4 can sing a 3-cost song", any(a[0] == "sing" for a in acts))
g.apply([a for a in acts if a[0] == "sing"][0])
check("Singer exerted after singing", bard.exerted)
# a plain cost-2 character could NOT sing it
g2 = fresh(); g2.active = 0
put(g2, "Aurora - Holding Court", 0, turn=1)                                # cost 1, no Singer
g2.players[0].hand.append(C("This Growing Pressure"))
check("non-Singer cost 1 can't sing cost 3", not any(a[0] == "sing" for a in g2.legal_actions()))

# 21. Printed Resist / Challenger in challenge math
g = fresh(); g.active = 0
resister = put(g, "Fat Cat - Felonious Feline", 1, exerted=True, turn=1)    # 6/6 Resist 1
chal = put(g, "Elinor - Bespelled Queen", 0, turn=1)                        # 3/4 Challenger +2
check("printed resist", g.eff_resist(resister) == 1)
check("printed challenger", AB.challenger_bonus(g, chal) == 2)
g.apply(("challenge", chal.uid, "char", resister.uid))
# damage to resister: 3 + 2 challenger - 1 resist = 4
check("challenge math with printed keywords", resister.damage == 4, resister.damage)

# 22. Printed Evasive blocks non-evasive challengers (pool card)
g = fresh(); g.active = 0
bashful = put(g, "Bashful - Riding the Rails", 1, exerted=True, turn=1)     # Evasive
att = put(g, "Buzz Lightyear - Space Ranger", 0, turn=1)
check("printed Evasive blocks challenge", ("char", bashful.uid) not in g.challenge_targets(att))

# 22b. Alert: can challenge Evasive defenders, but is NOT itself Evasive
g = fresh(); g.active = 0
evader = put(g, "Bashful - Riding the Rails", 1, exerted=True, turn=1)      # Evasive
alert_att = put(g, "Syndrome - Evil Inventor", 0, turn=1)                   # Alert
check("Alert attacker can challenge Evasive", ("char", evader.uid) in g.challenge_targets(alert_att))
plain_att = put(g, "Buzz Lightyear - Space Ranger", 0, turn=1)
check("plain attacker still can't reach Evasive", ("char", evader.uid) not in g.challenge_targets(plain_att))
# Alert character is itself challengeable by a plain attacker (not Evasive)
g2 = fresh(); g2.active = 1
alert_def = put(g2, "Syndrome - Evil Inventor", 0, exerted=True, turn=1)    # Alert, defending
opp_att = put(g2, "Beast - Snowfield Troublemaker", 1, turn=1)             # no Evasive
check("Alert is not itself Evasive (challengeable)",
      ("char", alert_def.uid) in g2.challenge_targets(opp_att))

# 22c. Ward: blocks TARGETED opponent effects, allows INDISCRIMINATE ones
# (a) targeted: _best_opp_char must skip a Warded character
g = fresh(); g.active = 0
warded = put(g, "Merida - Defiant Daughter", 1, turn=1)                     # Ward 2/3
plain = put(g, "Agustin Madrigal - Exceptionally Kind", 1, turn=1)          # no Ward 3/6
from lorcana import abilities as WB
ch_target = WB._best_opp_char(g, 0)
check("Ward: targeted effect skips Warded char", ch_target is not None and ch_target.uid == plain.uid)
# with only the Warded character present, a targeted effect finds NO legal target
g2 = fresh(); g2.active = 0
only_ward = put(g2, "Merida - Defiant Daughter", 1, turn=1)
check("Ward: targeted effect has no target if all Warded", WB._best_opp_char(g2, 0) is None)

# (b) indiscriminate: an effect that hits EVERY opposing character ignores Ward.
# Simulate a 'deal 2 damage to each opposing character' mass effect the way
# Under the Sea does -- iterate my_chars(opp) directly, no _best_opp_char.
g3 = fresh(); g3.active = 0
mward = put(g3, "Merida - Defiant Daughter", 1, turn=1)                      # Ward, W3
mplain = put(g3, "Agustin Madrigal - Exceptionally Kind", 1, turn=1)        # W6
for dc in list(g3.my_chars(1)):
    g3.deal_damage(dc, 2)
check("Ward: indiscriminate damage still hits Warded char", mward.damage == 2)
check("Ward: indiscriminate damage hits non-Warded too", mplain.damage == 2)

# (c) Ward does NOT stop challenges (that's Evasive); Warded char is challengeable
g4 = fresh(); g4.active = 1
wdef = put(g4, "Merida - Defiant Daughter", 0, exerted=True, turn=1)
watt = put(g4, "Beast - Snowfield Troublemaker", 1, turn=1)
check("Ward: still challengeable", ("char", wdef.uid) in g4.challenge_targets(watt))

# ============ PHASE 2: schema-driven abilities ===============================

# 23. Jessie PART OF A FAMILY via schema: conditional draw
g = fresh(); g.active = 0
jess = put(g, "Jessie - Lively Cowgirl", 0, turn=1)
put(g, "Alien - True Believer", 0, turn=1)
put(g, "Rex - Protective Dinosaur", 0, turn=1)   # 2 other Toys -> condition met
g.players[0].deck = list(deckB[:8]); g.players[0].hand = []
g.apply(("quest", jess.uid))
check("schema: Jessie draws with 2+ other Toys", len(g.players[0].hand) == 1)
g2 = fresh(); g2.active = 0
jess2 = put(g2, "Jessie - Lively Cowgirl", 0, turn=1)
put(g2, "Alien - True Believer", 0, turn=1)      # only 1 other Toy
g2.players[0].deck = list(deckB[:8]); g2.players[0].hand = []
g2.apply(("quest", jess2.uid))
check("schema: Jessie condition gates draw", len(g2.players[0].hand) == 0)

# 24. Elsa Concerned Sister THIS WAY via schema: location discount
g = fresh(); g.active = 0
g.players[0].hand = [C("Elsa - Concerned Sister"), C("Zootopia - Police Headquarters")]
g.players[0].ink_total = g.players[0].ink_ready = 4
g.apply([a for a in g.legal_actions() if a[0] == "play" and "Concerned" in a[1]][0])
check("schema: Elsa CS location discount", g.play_cost(0, C("Zootopia - Police Headquarters")) == 0)

# 16/25. Deck-out loss & 20-lore win
g = fresh(); g.active = 0
g.players[0].deck = []
g.draw(0, 1, forced=True)
check("deck-out loses", g.winner == 1)
g = fresh(); g.players[0].lore = 19
ch = put(g, "Aurora - Holding Court", 0, turn=1); g.active = 0
g.apply(("quest", ch.uid))
check("20 lore wins", g.winner == 0)

print(f"PASS {PASS}  FAIL {len(FAIL)}")
for f in FAIL: print("  FAIL:", f)
