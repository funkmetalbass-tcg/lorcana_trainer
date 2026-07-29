#!/usr/bin/env python3
"""
Lorcana game-log board-state extractor.

Reconstructs, after every turn, each player's:
  - lore
  - ink (total in inkwell; characters cost ink but Lorcana doesn't "spend" a
    persistent pool, so we track inkwell size, which is what matters for board state)
  - hand size
  - cards in play (with damage on each)
  - discard pile

Usage:
    python3 lorcana_boardstate.py <game_log.txt> [--cards master_legal_cardlist.json]
    python3 lorcana_boardstate.py <game_log.txt> --turn 8      # dump one turn
    python3 lorcana_boardstate.py <game_log.txt> --json out.json
"""

import argparse
import json
import re
import sys
from copy import deepcopy


def load_cards(path):
    if not path:
        return {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            cards = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}, {}
    # Case-insensitive index: log names use sentence case ("Strike a Good
    # Match") while DB keys are title-cased ("Strike A Good Match").
    index = {k.lower(): k for k in cards}
    return cards, index


def lookup(cards, index, name):
    """Return the card dict for `name`, matching case-insensitively."""
    if name in cards:
        return cards[name]
    key = index.get(name.lower())
    return cards.get(key) if key else None


def willpower(cards, index, name):
    """Look up a character's printed willpower; None if unknown/non-character."""
    c = lookup(cards, index, name)
    if not c:
        return None
    wp = c.get("Willpower")
    try:
        return int(wp)
    except (TypeError, ValueError):
        return None


class Player:
    def __init__(self, label):
        self.label = label
        self.lore = 0
        self.ink = 0                # cards in inkwell
        self.hand = 7               # starting hand; mulligan is 1-for-1, no change
        self.play = {}              # name -> {"damage": int, "count": int}
        self.discard = []           # list of card names

    def add_to_play(self, name):
        e = self.play.setdefault(name, {"damage": 0, "count": 0})
        e["count"] += 1

    def remove_from_play(self, name):
        e = self.play.get(name)
        if not e:
            return
        e["count"] -= 1
        e["damage"] = 0
        if e["count"] <= 0:
            del self.play[name]

    def snapshot(self):
        play = {}
        for name, e in self.play.items():
            for _ in range(e["count"]):
                play.setdefault(name, []).append(e["damage"])
        return {
            "lore": self.lore,
            "ink": self.ink,
            "hand": self.hand,
            "cards_in_play": play,          # name -> [damage per copy]
            "discard": list(self.discard),
        }


# --- line patterns -----------------------------------------------------------
RE_TURN      = re.compile(r"^--- Turn (\d+) ---")
RE_BEGIN     = re.compile(r"^(Player \d+)'s turn begins")
RE_INK       = re.compile(r"^(Player \d+) added .+? to ink")
RE_PLAY      = re.compile(r"^(Player \d+) played (.+?) \(cost \d+\)")
RE_QUEST     = re.compile(r"^(Player \d+) quested with .+?\(\+\d+ \[LORE\], \d+ -> (\d+)\)")
RE_WON       = re.compile(r"^(Player \d+) won with (\d+) \[LORE\]")
RE_BANISHED  = re.compile(r"^(.+?) was banished$")
RE_DEALT     = re.compile(r"dealt (\d+) damage to (.+?)$")
RE_DMG_CTR   = re.compile(r"^(\d+) damage counter[s]? put on (.+?)$")
RE_DISCARD   = re.compile(r"^(Player \d+) discarded (.+?) from hand")
RE_SHIFT     = re.compile(r"^(Player \d+) shifted (.+?) onto (.+?) \(Shift")
# hand +1 events
RE_DREW1     = re.compile(r"^(Player \d+) drew (?!\d+ cards)(.+)$")
RE_DREWN     = re.compile(r"^(Player \d+) drew (\d+) cards:")
RE_TOHAND    = re.compile(r"^(Player \d+) revealed .+? put into hand")
# hand -1 events (played/added/discarded already handled above; these are extra)
RE_SANG      = re.compile(r"^(Player \d+) sang (.+?) with ")
RE_BOOSTED   = re.compile(r"^(Player \d+) boosted ")
# challenge summary carries the current damage state of the defender:
#   ... dealt X dmg to NAME (cur/max [WILLPOWER]...), took Y dmg (cur/max [WILLPOWER]...)
RE_CHAL_LINE = re.compile(
    r"dealt \d+ dmg to (.+?) \((\d+)/\d+ \[WILLPOWER\].*?\), took \d+ dmg \((\d+)/\d+"
)


def infer_owner(players, name, prefer=None):
    """Which player controls a character named `name`? Prefer the acting player."""
    owners = [p for p in players.values() if name in p.play]
    if not owners:
        return None
    if prefer and prefer in [o.label for o in owners]:
        return next(o for o in owners if o.label == prefer)
    return owners[0]


def parse(log_path, cards, index):
    players = {"Player 1": Player("Player 1"), "Player 2": Player("Player 2")}
    active = None
    turns = []          # list of (turn_no, active_label, {label: snapshot})
    turn_no = None

    with open(log_path, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]

    def flush(tn, act):
        turns.append((tn, act, {lbl: p.snapshot() for lbl, p in players.items()}))

    for ln in lines:
        m = RE_TURN.match(ln)
        if m:
            if turn_no is not None:
                flush(turn_no, active)
            turn_no = int(m.group(1))
            continue

        m = RE_BEGIN.match(ln)
        if m:
            active = m.group(1)
            continue

        m = RE_INK.match(ln)
        if m:
            p = players[m.group(1)]
            p.ink += 1
            p.hand -= 1
            continue

        m = RE_DREWN.match(ln)
        if m:
            players[m.group(1)].hand += int(m.group(2))
            continue

        m = RE_TOHAND.match(ln)
        if m:
            players[m.group(1)].hand += 1
            continue

        m = RE_DREW1.match(ln)
        if m:
            players[m.group(1)].hand += 1
            continue

        m = RE_SANG.match(ln)
        if m:
            pl, song = m.group(1), m.group(2)
            p = players[pl]
            p.hand -= 1              # song leaves hand
            p.discard.append(song)   # and goes to discard
            continue

        m = RE_BOOSTED.match(ln)
        if m:
            # Boost puts the TOP CARD OF THE DECK under the character, not a
            # card from hand — so hand size is unchanged.
            continue

        m = RE_PLAY.match(ln)
        if m:
            pl, name = m.group(1), m.group(2)
            players[pl].hand -= 1   # card leaves hand when played
            # Actions (incl. songs) go to discard, not the board; characters,
            # items, and locations stay in play.
            ctype = (lookup(cards, index, name) or {}).get("CardType", "")
            if ctype == "Action":
                players[pl].discard.append(name)
            else:
                players[pl].add_to_play(name)
            continue

        m = RE_SHIFT.match(ln)
        if m:
            pl, top, base = m.group(1), m.group(2), m.group(3)
            p = players[pl]
            p.hand -= 1                  # shift card comes from hand
            p.remove_from_play(base)     # base is covered by the shifted card
            p.add_to_play(top)
            continue

        m = RE_QUEST.match(ln)
        if m:
            players[m.group(1)].lore = int(m.group(2))
            continue

        m = RE_WON.match(ln)
        if m:
            players[m.group(1)].lore = int(m.group(2))
            continue

        m = RE_DISCARD.match(ln)
        if m:
            p = players[m.group(1)]
            p.hand -= 1
            p.discard.append(m.group(2))
            continue

        # Damage from named-effect lines: "X dealt N damage to Y"
        m = RE_DEALT.search(ln)
        if m and " dealt " in ln and "[WILLPOWER]" not in ln:
            dmg, target = int(m.group(1)), m.group(2)
            owner = infer_owner(players, target)
            if owner:
                owner.play[target]["damage"] += dmg
            continue

        m = RE_DMG_CTR.match(ln)
        if m:
            dmg, target = int(m.group(1)), m.group(2)
            owner = infer_owner(players, target)
            if owner:
                owner.play[target]["damage"] += dmg
            continue

        # Challenge summary lines set damage explicitly for both combatants.
        m = RE_CHAL_LINE.search(ln)
        if m:
            defender, dfdmg, atkdmg = m.group(1), int(m.group(2)), int(m.group(3))
            od = infer_owner(players, defender)
            if od:
                od.play[defender]["damage"] = dfdmg
            # attacker damage: find the "with <attacker>" earlier on challenge header
            ma = re.search(r"challenged .+? with (.+?)$", ln.split("|")[0])
            if ma:
                atk = ma.group(1).strip()
                oa = infer_owner(players, atk, prefer=active)
                if oa:
                    oa.play[atk]["damage"] = atkdmg
            continue

        m = RE_BANISHED.match(ln)
        if m:
            name = m.group(1)
            # banished character leaves play -> discard, for whichever player has it
            owner = infer_owner(players, name)
            if owner:
                owner.remove_from_play(name)
                owner.discard.append(name)
            continue

    if turn_no is not None:
        flush(turn_no, active)
    return turns


def fmt_play(play):
    if not play:
        return "    (empty)"
    out = []
    for name, dmgs in sorted(play.items()):
        for d in dmgs:
            tag = f"  [{d} dmg]" if d else ""
            out.append(f"    - {name}{tag}")
    return "\n".join(out)


def report(turns, only_turn=None):
    lines = []
    for tn, act, snap in turns:
        if only_turn is not None and tn != only_turn:
            continue
        lines.append(f"=== After Turn {tn} (active: {act}) ===")
        for lbl in ("Player 1", "Player 2"):
            s = snap[lbl]
            lines.append(f"  {lbl}:  lore={s['lore']}  ink={s['ink']}  "
                         f"hand={s['hand']}  discard={len(s['discard'])}")
            lines.append("  cards in play:")
            lines.append(fmt_play(s["cards_in_play"]))
            if s["discard"]:
                lines.append(f"  discard: {', '.join(s['discard'])}")
        lines.append("")
    return "\n".join(lines)


def board_block(snap):
    """Format one turn's board state in the interleaved log style."""
    lines = ["Board State---"]
    for lbl in ("Player 1", "Player 2"):
        s = snap[lbl]
        lines.append(f"  {lbl}:  lore={s['lore']}  ink={s['ink']}  "
                     f"hand={s['hand']}  discard={len(s['discard'])}")
        lines.append("  cards in play:")
        lines.append(fmt_play(s["cards_in_play"]))
        if s["discard"]:
            lines.append(f"  discard: {', '.join(s['discard'])}")
    return "\n".join(lines)


def annotate(log_path, turns):
    """Return the original log with a board-state block inserted after each
    turn, placed right before the next '--- Turn N ---' header (or at EOF)."""
    with open(log_path, encoding="utf-8") as f:
        src = [ln.rstrip("\n") for ln in f]

    # Map: turn number -> its snapshot.
    by_turn = {tn: snap for tn, _, snap in turns}

    out = []
    cur = None  # turn number of the block we're currently inside
    for ln in src:
        m = RE_TURN.match(ln)
        if m:
            # Before starting a new turn header, flush the previous turn's board.
            if cur is not None and cur in by_turn:
                out.append(board_block(by_turn[cur]))
                out.append("")  # blank line before next turn header
            cur = int(m.group(1))
        out.append(ln)

    # Flush the final turn's board at EOF.
    if cur is not None and cur in by_turn:
        out.append(board_block(by_turn[cur]))

    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--cards", default="/mnt/project/master_legal_cardlist.json")
    ap.add_argument("--turn", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--annotate", default=None,
                    help="write the log with board-state blocks interleaved to this path")
    args = ap.parse_args()

    cards, index = load_cards(args.cards)
    turns = parse(args.log, cards, index)

    if args.annotate:
        text = annotate(args.log, turns)
        with open(args.annotate, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.annotate}")
    elif args.json:
        payload = [{"turn": tn, "active": act, "state": snap}
                   for tn, act, snap in turns]
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"wrote {args.json}")
    else:
        print(report(turns, args.turn))


if __name__ == "__main__":
    main()
