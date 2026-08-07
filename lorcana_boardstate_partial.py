#!/usr/bin/env python3
"""
Board-state extractor for PARTIAL / in-progress Lorcana logs.

In-progress logs differ from finished-game logs:
  - every event line has a trailing timestamp:  "...· 3m" / "...· 20s"
  - turn headers are "Turn 1" not "--- Turn 1 ---"
  - players are "You"/"Opponent" (and "Your"/"Opponent's") not Player 1/2
  - lore is written "+1 Lore, 0 -> 1" not "+1 [LORE], 0 -> 1"
  - opponent info is hidden: "Opponent drew a card" (no name),
    "Opponent mulliganed 7 cards" (no list)
  - the log can stop mid-turn (no "won" line)

Strategy: NORMALIZE each partial line into the finished-game grammar, then
reuse the exact parser in lorcana_boardstate.py so the game logic lives in one
place. Hidden opponent draws are emitted as a synthetic named draw so the hand
counter still increments; the card name is unknown so it's marked as such.

Usage:
    python3 lorcana_boardstate_partial.py <log.txt> [--cards cards.json]
    python3 lorcana_boardstate_partial.py <log.txt> --annotate out.txt
    python3 lorcana_boardstate_partial.py <log.txt> --annotate -   # to stdout
    python3 lorcana_boardstate_partial.py <log.txt> --json out.json
    python3 lorcana_boardstate_partial.py <log.txt> --turn 3
"""

import argparse
import json
import re
import sys

import lorcana_boardstate as base


# You = Player 1, Opponent = Player 2 (arbitrary but fixed mapping).
YOU, OPP = "Player 1", "Player 2"

# Strip trailing "· 3m", "· 20s", "· 1h", etc. The dot is U+00B7.
RE_TS = re.compile(r"\s*[·]\s*\d+[a-z]+\s*$")

_UNKNOWN = 0  # counter for synthesizing unique unknown-card names


def _who(word):
    """Map a possessive/subject token to a canonical player label."""
    return YOU if word in ("You", "Your") else OPP


def normalize(lines):
    """Convert partial-log lines into finished-game grammar lines."""
    global _UNKNOWN
    out = []
    for raw in lines:
        ln = RE_TS.sub("", raw.rstrip("\n")).rstrip()
        if not ln:
            continue

        # --- turn header ---
        m = re.match(r"^Turn (\d+)$", ln)
        if m:
            out.append(f"--- Turn {m.group(1)} ---")
            continue

        # --- turn begins ---
        m = re.match(r"^(Your|Opponent's) turn begins$", ln)
        if m:
            who = YOU if m.group(1) == "Your" else OPP
            out.append(f"{who}'s turn begins")
            continue

        # --- ended turn (normalize to canonical, though parser ignores it) ---
        m = re.match(r"^(You|Opponent) ended (your|their) turn$", ln)
        if m:
            who = _who(m.group(1))
            out.append(f"{who} ended {who}'s turn")
            continue

        # --- hidden opponent draw: "Opponent drew a card" ---
        if ln == "Opponent drew a card":
            _UNKNOWN += 1
            out.append(f"{OPP} drew ??? unknown-{_UNKNOWN}")
            continue

        # --- named draw: "You drew X" / "Opponent drew X" ---
        m = re.match(r"^(You|Opponent) drew (\d+) cards: (.+)$", ln)
        if m:
            who = _who(m.group(1))
            out.append(f"{who} drew {m.group(2)} cards: {m.group(3)}")
            continue
        m = re.match(r"^(You|Opponent) drew (.+)$", ln)
        if m:
            who = _who(m.group(1))
            out.append(f"{who} drew {m.group(2)}")
            continue

        # --- ink ---
        m = re.match(r"^(You|Opponent) added (.+?) to ink$", ln)
        if m:
            out.append(f"{_who(m.group(1))} added {m.group(2)} to ink")
            continue

        # --- play ---
        m = re.match(r"^(You|Opponent) played (.+? \(cost \d+\))$", ln)
        if m:
            out.append(f"{_who(m.group(1))} played {m.group(2)}")
            continue

        # --- quest: "+1 Lore, 0 -> 1" -> "+1 [LORE], 0 -> 1" ---
        m = re.match(r"^(You|Opponent) quested with (.+?) \(\+(\d+) Lore, (\d+) -> (\d+)\)$", ln)
        if m:
            who = _who(m.group(1))
            out.append(f"{who} quested with {m.group(2)} "
                       f"(+{m.group(3)} [LORE], {m.group(4)} -> {m.group(5)})")
            continue

        # --- discard from hand ---
        m = re.match(r"^(You|Opponent) discarded (.+?) from hand$", ln)
        if m:
            out.append(f"{_who(m.group(1))} discarded {m.group(2)} from hand")
            continue

        # --- sang ---
        m = re.match(r"^(You|Opponent) sang (.+?) with (.+)$", ln)
        if m:
            out.append(f"{_who(m.group(1))} sang {m.group(2)} with {m.group(3)}")
            continue

        # --- shifted ---
        m = re.match(r"^(You|Opponent) shifted (.+? onto .+? \(Shift.+)$", ln)
        if m:
            out.append(f"{_who(m.group(1))} shifted {m.group(2)}")
            continue

        # --- boosted ---
        m = re.match(r"^(You|Opponent) boosted (.+)$", ln)
        if m:
            out.append(f"{_who(m.group(1))} boosted {m.group(2)}")
            continue

        # --- challenge header + summary ---
        m = re.match(r"^(You|Opponent) challenged (.+)$", ln)
        if m:
            out.append(f"{_who(m.group(1))} challenged {m.group(2)}")
            continue

        # --- lore-form lore inside other lines (e.g. quest already handled) ---
        # Convert any residual "[N] Lore" -> "[LORE]" and pass through
        # (covers "won with N Lore!" if a partial log ever contains it).
        m = re.match(r"^(You|Opponent) won with (\d+) Lore", ln)
        if m:
            out.append(f"{_who(m.group(1))} won with {m.group(2)} [LORE]!")
            continue

        # --- lines that need no player remap but may carry Lore wording ---
        passthru = ln.replace(" Lore,", " [LORE],").replace(" Lore!", " [LORE]!")
        out.append(passthru)

    return out


def load_partial(log_path, cards, index):
    with open(log_path, encoding="utf-8") as f:
        raw = f.readlines()
    norm = normalize(raw)
    # Feed normalized lines through the base parser by writing to a temp buffer
    # in memory: replicate base.parse but over an in-memory list.
    return _parse_lines(norm, cards, index)


def _parse_lines(lines, cards, index):
    """Mirror of base.parse but consuming a list of normalized lines."""
    players = {YOU: base.Player(YOU), OPP: base.Player(OPP)}
    active = None
    turns = []
    turn_no = None

    def flush(tn, act):
        turns.append((tn, act, {lbl: p.snapshot() for lbl, p in players.items()}))

    for ln in lines:
        m = base.RE_TURN.match(ln)
        if m:
            if turn_no is not None:
                flush(turn_no, active)
            turn_no = int(m.group(1))
            continue
        m = base.RE_BEGIN.match(ln)
        if m:
            active = m.group(1); continue
        m = base.RE_INK.match(ln)
        if m:
            p = players[m.group(1)]; p.ink += 1; p.hand -= 1; continue
        m = base.RE_DREWN.match(ln)
        if m:
            players[m.group(1)].hand += int(m.group(2)); continue
        m = base.RE_TOHAND.match(ln)
        if m:
            players[m.group(1)].hand += 1; continue
        m = base.RE_DREW1.match(ln)
        if m:
            players[m.group(1)].hand += 1; continue
        m = base.RE_SANG.match(ln)
        if m:
            pl, song = m.group(1), m.group(2)
            p = players[pl]; p.hand -= 1; p.discard.append(song); continue
        m = base.RE_BOOSTED.match(ln)
        if m:
            continue  # boost is from deck, no hand change
        m = base.RE_PLAY.match(ln)
        if m:
            pl, name = m.group(1), m.group(2)
            players[pl].hand -= 1
            ctype = (base.lookup(cards, index, name) or {}).get("CardType", "")
            if ctype == "Action":
                players[pl].discard.append(name)
            else:
                players[pl].add_to_play(name)
            continue
        m = base.RE_SHIFT.match(ln)
        if m:
            pl, top, bse = m.group(1), m.group(2), m.group(3)
            p = players[pl]; p.hand -= 1
            p.remove_from_play(bse); p.add_to_play(top); continue
        m = base.RE_QUEST.match(ln)
        if m:
            players[m.group(1)].lore = int(m.group(2)); continue
        m = base.RE_WON.match(ln)
        if m:
            players[m.group(1)].lore = int(m.group(2)); continue
        m = base.RE_DISCARD.match(ln)
        if m:
            p = players[m.group(1)]; p.hand -= 1
            p.discard.append(m.group(2)); continue
        m = base.RE_DEALT.search(ln)
        if m and " dealt " in ln and "[WILLPOWER]" not in ln:
            dmg, target = int(m.group(1)), m.group(2)
            o = base.infer_owner(players, target)
            if o: o.play[target]["damage"] += dmg
            continue
        m = base.RE_DMG_CTR.match(ln)
        if m:
            dmg, target = int(m.group(1)), m.group(2)
            o = base.infer_owner(players, target)
            if o: o.play[target]["damage"] += dmg
            continue
        m = base.RE_CHAL_LINE.search(ln)
        if m:
            defender, dfdmg, atkdmg = m.group(1), int(m.group(2)), int(m.group(3))
            od = base.infer_owner(players, defender)
            if od: od.play[defender]["damage"] = dfdmg
            ma = re.search(r"challenged .+? with (.+?)$", ln.split("|")[0])
            if ma:
                atk = ma.group(1).strip()
                oa = base.infer_owner(players, atk, prefer=active)
                if oa: oa.play[atk]["damage"] = atkdmg
            continue
        m = base.RE_BANISHED.match(ln)
        if m:
            name = m.group(1)
            o = base.infer_owner(players, name)
            if o:
                o.remove_from_play(name); o.discard.append(name)
            continue

    if turn_no is not None:
        flush(turn_no, active)
    return turns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--cards", default="/mnt/project/master_legal_cardlist.json")
    ap.add_argument("--turn", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--annotate", default=None,
                    help="write log with board-state blocks; use '-' for stdout")
    args = ap.parse_args()

    cards, index = base.load_cards(args.cards)
    turns = load_partial(args.log, cards, index)

    if args.annotate:
        # Re-read raw, strip timestamps, and interleave board blocks.
        with open(args.log, encoding="utf-8") as f:
            raw = [RE_TS.sub("", ln.rstrip("\n")).rstrip() for ln in f]
        by_turn = {tn: snap for tn, _, snap in turns}
        out, cur = [], None
        for ln in raw:
            m = re.match(r"^Turn (\d+)$", ln)
            if m:
                if cur is not None and cur in by_turn:
                    out.append(base.board_block(by_turn[cur])); out.append("")
                cur = int(m.group(1))
            out.append(ln)
        if cur is not None and cur in by_turn:
            out.append(base.board_block(by_turn[cur]))
        text = "\n".join(out) + "\n"
        if args.annotate == "-":
            sys.stdout.write(text)
        else:
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
        print(base.report(turns, args.turn))


if __name__ == "__main__":
    main()
