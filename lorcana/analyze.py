"""Deck diagnostics.

Runs many games and, for ONE deck under study (by default Deck A, seat 0 when
it's on the play and seat 1 when on the draw), records public information each
game to attribute results to individual cards and to summarize how games are
lost. Nothing here touches engine internals or MCTS rollouts, so it doesn't
slow search or perturb play.

Metrics per card:
  seen%      games where >=1 copy was drawn into hand (excludes opening only? no:
             includes opening hand + all draws)
  play%      of games where seen, fraction where >=1 copy was actually played
  dead%      of games where seen, fraction where a copy was still stuck in hand
             at game end (never played, never inked)
  ink%       fraction of seen games where a copy was spent as ink
  wr_played  win rate of games where the card was played at least once
  wr_unseen  win rate of games where the card was never drawn
  delta      wr_played - wr_unseen  (positive => card correlates with winning)

Tempo/curve:
  ink-per-turn curve, average turn each cost bucket first hits the board,
  flood/screw rates (too many lands-in-hand / not enough ink).

Loss patterns:
  fast vs grind losses, average lore gap in losses, whether losses correlate
  with location glut or ink screw.
"""
import random
from collections import defaultdict

from .engine import Game
from .policies import default_mulligan
from . import mcts


def make_policy(name, iters, seed):
    from .cli import make_policy as _mp
    return _mp(name, iters, seed)


class DeckTracker:
    """Tracks one seat's card usage across a single game."""

    def __init__(self, game, seat, decklist):
        self.game = game
        self.seat = seat
        # multiset of card names in the starting deck
        self.deck_names = [c.name for c in decklist]
        self.unique = sorted(set(self.deck_names))
        self.copies = defaultdict(int)
        for n in self.deck_names:
            self.copies[n] += 1
        # per-game observations
        self.seen = set()        # names drawn into hand at any point
        self.played = set()      # names played (character/loc/action/sung)
        self.inked = set()       # names spent as ink
        self._known_hand = set() # last-seen hand contents (names)
        # tempo
        self.ink_by_turn = {}    # our-turn index -> ink_total after our turn
        self.first_play_turn = {}  # cost bucket -> earliest our-turn a card of that cost hit board
        self.our_turns = 0
        # opening
        self.opening_hand = None
        self.opening_inkable = None

    def note_opening(self):
        hand = self.game.players[self.seat].hand
        self.opening_hand = [c.name for c in hand]
        self.opening_inkable = sum(1 for c in hand if c.inkable)
        self.seen.update(self.opening_hand)
        self._known_hand = set(self.opening_hand)

    def observe_before(self, action, actor):
        """Called before an action by `actor` is applied."""
        if actor != self.seat:
            return
        kind = action[0]
        if kind == "ink":
            self.inked.add(action[1])
        elif kind == "play":
            self.played.add(action[1])
            self._record_curve(action[1])
        elif kind in ("sing", "sing_together"):
            self.played.add(action[1])

    def _record_curve(self, name):
        card = self.game.db_get(name) if hasattr(self.game, "db_get") else None
        # fall back: find cost from any hand copy
        cost = None
        for c in self.game.players[self.seat].hand:
            if c.name == name:
                cost = c.cost
                break
        if cost is None:
            return
        bucket = min(cost, 7)
        if bucket not in self.first_play_turn:
            self.first_play_turn[bucket] = self.our_turns + 1

    def observe_after_our_turn(self):
        self.our_turns += 1
        pl = self.game.players[self.seat]
        self.ink_by_turn[self.our_turns] = pl.ink_total
        # update seen with any new hand cards (draws happen at turn start)
        for c in pl.hand:
            self.seen.add(c.name)

    def finalize(self):
        pl = self.game.players[self.seat]
        stuck = set(c.name for c in pl.hand)
        # dead = drawn, never played, never inked, still in hand at end
        self.dead = {n for n in stuck if n not in self.played and n not in self.inked}
        self.final_hand_size = len(pl.hand)
        self.final_locations_in_hand = sum(1 for c in pl.hand if c.is_location)


def _run_tracked_game(deckU, deckO, polU, polO, seed, study_on_seat0):
    """Run one game; deckU/polU is the deck under study. Returns (won, tracker).
    study_on_seat0 controls which physical seat the studied deck occupies."""
    if study_on_seat0:
        g = Game(deckU, deckO, seed=seed)
        seat = 0
    else:
        g = Game(deckO, deckU, seed=seed)
        seat = 1
    from collections import Counter as _Counter
    g.trig = _Counter()
    g.start(mulligan_fn=lambda game, p: default_mulligan(game, p))
    tracker = DeckTracker(g, seat, deckU)
    tracker.note_opening()
    rng = random.Random(seed)
    turn_cap = 120
    last_active = g.active
    while g.winner is None and g.turn < turn_cap:
        actor = g.active
        pol = polU if actor == seat else polO
        a = pol(g, rng)
        tracker.observe_before(a, actor)
        was_active = g.active
        g.apply(a)
        # detect end of the studied seat's turn (active switched away from seat)
        if was_active == seat and g.active != seat:
            tracker.observe_after_our_turn()
    if g.winner is None and g.players[0].lore != g.players[1].lore:
        g.winner = 0 if g.players[0].lore > g.players[1].lore else 1
    tracker.finalize()
    won = (g.winner == seat)
    return won, tracker, g, seat


def analyze_deck(db, deckU, deckO, policyU, policyO, games, iters, seed,
                 label_u="Deck A", label_o="Deck B", progress=10):
    """Aggregate diagnostics over `games` games. Alternates play/draw."""
    polU = make_policy(policyU, iters, seed=1)
    polO = make_policy(policyO, iters, seed=2)

    n_seen = defaultdict(int)
    n_played = defaultdict(int)
    n_dead = defaultdict(int)
    n_inked = defaultdict(int)
    wins_when_played = defaultdict(int)
    games_when_played = defaultdict(int)
    wins_when_unseen = defaultdict(int)
    games_when_unseen = defaultdict(int)

    total_wins = 0
    loss_lengths = []
    win_lengths = []
    loss_gaps = []
    flood_losses = 0
    screw_losses = 0
    trig_total = defaultdict(int)
    trig_games = defaultdict(int)
    ink_curve_sum = defaultdict(float)
    ink_curve_cnt = defaultdict(int)
    first_play_sum = defaultdict(float)
    first_play_cnt = defaultdict(int)
    opening_inkable_hist = defaultdict(int)

    copies = None

    for i in range(games):
        study_seat0 = (i % 2 == 0)
        won, tr, g, seat = _run_tracked_game(
            deckU, deckO, polU, polO, seed + i, study_seat0)
        if progress and (i + 1) % progress == 0:
            import sys
            sys.stderr.write(f"\r  ...{i+1}/{games} games "
                             f"(win rate so far {100.0*total_wins/max(1,i):.0f}%)  ")
            sys.stderr.flush()
        if copies is None:
            copies = tr.copies
            unique = tr.unique
        total_wins += won

        for (tp, label), n in getattr(g, "trig", {}).items():
            if tp == seat:
                trig_total[label] += n
                trig_games[label] += 1

        for name in unique:
            seen = name in tr.seen
            if seen:
                n_seen[name] += 1
                if name in tr.played:
                    n_played[name] += 1
                    games_when_played[name] += 1
                    wins_when_played[name] += won
                if name in tr.dead:
                    n_dead[name] += 1
                if name in tr.inked:
                    n_inked[name] += 1
            else:
                games_when_unseen[name] += 1
                wins_when_unseen[name] += won

        opp = g.players[1 - seat]
        me = g.players[seat]
        if won:
            win_lengths.append(g.turn)
        else:
            loss_lengths.append(g.turn)
            loss_gaps.append(opp.lore - me.lore)
            if tr.final_locations_in_hand >= 3:
                flood_losses += 1
            if tr.opening_inkable is not None and tr.opening_inkable <= 2:
                screw_losses += 1
        for t, v in tr.ink_by_turn.items():
            ink_curve_sum[t] += v
            ink_curve_cnt[t] += 1
        for bucket, t in tr.first_play_turn.items():
            first_play_sum[bucket] += t
            first_play_cnt[bucket] += 1
        if tr.opening_inkable is not None:
            opening_inkable_hist[tr.opening_inkable] += 1

    return {
        "games": games, "wins": total_wins,
        "unique": unique, "copies": copies,
        "n_seen": n_seen, "n_played": n_played, "n_dead": n_dead, "n_inked": n_inked,
        "wins_when_played": wins_when_played, "games_when_played": games_when_played,
        "wins_when_unseen": wins_when_unseen, "games_when_unseen": games_when_unseen,
        "loss_lengths": loss_lengths, "win_lengths": win_lengths, "loss_gaps": loss_gaps,
        "flood_losses": flood_losses, "screw_losses": screw_losses,
        "ink_curve_sum": ink_curve_sum, "ink_curve_cnt": ink_curve_cnt,
        "first_play_sum": first_play_sum, "first_play_cnt": first_play_cnt,
        "opening_inkable_hist": opening_inkable_hist,
        "trig_total": dict(trig_total), "trig_games": dict(trig_games),
        "label_u": label_u, "label_o": label_o,
    }


def _rate(num, den):
    return (100.0 * num / den) if den else 0.0


def format_report(R):
    L = []
    g = R["games"]; w = R["wins"]
    L.append(f"\n{'='*78}")
    L.append(f"DECK DIAGNOSTICS: {R['label_u']} over {g} games  "
             f"(win rate {_rate(w, g):.0f}%)")
    L.append("="*78)

    # ---- per-card table ----
    L.append("\nPER-CARD CONTRIBUTION")
    L.append("  play%/dead%/ink% are among games where the card was drawn.")
    L.append("  delta = winrate(played) - winrate(never drawn); "
             "negative = underperforming.\n")
    header = (f"  {'card':38s} {'cop':>3s} {'seen':>5s} {'play':>5s} "
              f"{'dead':>5s} {'ink':>4s} {'wr+':>5s} {'wr0':>5s} {'delta':>6s}")
    L.append(header)
    L.append("  " + "-"*(len(header)-2))
    rows = []
    for name in R["unique"]:
        seen = R["n_seen"][name]
        played = R["n_played"][name]
        dead = R["n_dead"][name]
        inked = R["n_inked"][name]
        wr_played = _rate(R["wins_when_played"][name], R["games_when_played"][name])
        wr_unseen = _rate(R["wins_when_unseen"][name], R["games_when_unseen"][name])
        has_played = R["games_when_played"][name] > 0
        has_unseen = R["games_when_unseen"][name] > 0
        delta = (wr_played - wr_unseen) if (has_played and has_unseen) else None
        rows.append((name, R["copies"][name], seen, played, dead, inked,
                     wr_played, wr_unseen, delta, has_played, has_unseen))
    # sort worst-delta first to surface cut candidates
    def sortkey(r):
        d = r[8]
        return (0, d) if d is not None else (1, 0)
    for (name, cop, seen, played, dead, inked, wrp, wru, delta,
         hp, hu) in sorted(rows, key=sortkey):
        seenp = _rate(seen, g)
        playp = _rate(played, seen)
        deadp = _rate(dead, seen)
        inkp = _rate(inked, seen)
        wrp_s = f"{wrp:4.0f}" if hp else "  - "
        wru_s = f"{wru:4.0f}" if hu else "  - "
        delta_s = f"{delta:+5.0f}" if delta is not None else "    -"
        L.append(f"  {name[:38]:38s} {cop:3d} {seenp:4.0f}% {playp:4.0f}% "
                 f"{deadp:4.0f}% {inkp:3.0f}% {wrp_s}% {wru_s}% {delta_s}")

    # ---- flags ----
    L.append("\nFLAGS (heuristic cut/keep signals)")
    flagged = False
    for (name, cop, seen, played, dead, inked, wrp, wru, delta,
         hp, hu) in sorted(rows, key=sortkey):
        seenp = _rate(seen, g)
        playp = _rate(played, seen)
        deadp = _rate(dead, seen)
        inkp = _rate(inked, seen)
        notes = []
        if delta is not None and delta <= -8:
            notes.append(f"win-rate drag ({delta:+.0f})")
        if playp < 45 and inkp < 60:
            notes.append(f"low utilization (played {playp:.0f}% when drawn)")
        if deadp >= 30:
            notes.append(f"often dead in hand ({deadp:.0f}%)")
        if inkp >= 75:
            notes.append(f"mostly inked ({inkp:.0f}%) — may be a filler slot")
        if notes:
            flagged = True
            L.append(f"  - {name}: " + "; ".join(notes))
    if not flagged:
        L.append("  (no cards tripped the cut thresholds)")

    # ---- tempo / curve ----
    L.append("\nTEMPO & CURVE")
    ink_line = []
    for t in sorted(R["ink_curve_cnt"]):
        if t > 10:
            break
        avg = R["ink_curve_sum"][t] / R["ink_curve_cnt"][t]
        ink_line.append(f"T{t}:{avg:.1f}")
    L.append("  Avg ink after your turn:  " + "  ".join(ink_line))
    fp = []
    for bucket in sorted(R["first_play_cnt"]):
        avg = R["first_play_sum"][bucket] / R["first_play_cnt"][bucket]
        label = f"{bucket}+" if bucket == 7 else str(bucket)
        fp.append(f"{label}-drop: turn {avg:.1f}")
    L.append("  Avg turn each cost first lands: " + "  ".join(fp))
    oh = R["opening_inkable_hist"]
    if oh:
        tot = sum(oh.values())
        dist = "  ".join(f"{k}:{_rate(v,tot):.0f}%" for k, v in sorted(oh.items()))
        L.append(f"  Opening-hand inkable count distribution: {dist}")

    # ---- loss patterns ----
    L.append("\nLOSS PATTERNS")
    ll = R["loss_lengths"]; wl = R["win_lengths"]
    if ll:
        avg_l = sum(ll) / len(ll)
        fast = sum(1 for x in ll if x <= 14)
        grind = sum(1 for x in ll if x >= 24)
        L.append(f"  Losses: {len(ll)}   avg length {avg_l:.1f} half-turns "
                 f"(fast <=14: {fast}, grind >=24: {grind})")
        if R["loss_gaps"]:
            L.append(f"  Avg lore gap in losses: "
                     f"{sum(R['loss_gaps'])/len(R['loss_gaps']):.1f}")
        L.append(f"  Losses with 3+ locations stuck in hand (flood): "
                 f"{R['flood_losses']}  ({_rate(R['flood_losses'], len(ll)):.0f}% of losses)")
        L.append(f"  Losses after a low-ink opening (<=2 inkable): "
                 f"{R['screw_losses']}  ({_rate(R['screw_losses'], len(ll)):.0f}% of losses)")
        # interpretation hint
        L.append("\nREAD")
        if fast > grind and fast >= len(ll) * 0.45:
            L.append("  Most losses are FAST — you're being raced. Consider cheaper "
                     "early defense / more lore-per-turn early, and trimming top-end.")
        elif grind > fast and grind >= len(ll) * 0.45:
            L.append("  Most losses are GRINDS — you run out of gas. Consider more "
                     "card advantage / reach / a resilient late threat.")
        else:
            L.append("  Losses are mixed in length — no single tempo problem dominates; "
                     "lean on the per-card deltas and flood/screw rates below.")
        if _rate(R['flood_losses'], len(ll)) >= 30:
            L.append("  Location flooding shows up in many losses — consider cutting "
                     "1-2 locations or adding a payoff that uses them faster.")
        if _rate(R['screw_losses'], len(ll)) >= 40:
            L.append("  Ink screw is frequent — raise your inkable count "
                     "(see opening distribution) or lower the curve.")
    else:
        L.append("  No losses recorded.")

    if R.get("trig_total"):
        L.append("\nABILITY TRIGGERS (studied deck; totals across all games)")
        L.append(f"  {'trigger':<44} {'total':>6} {'avg/game':>9} {'games%':>7}")
        for label in sorted(R["trig_total"], key=lambda k: -R["trig_total"][k]):
            tot = R["trig_total"][label]
            gp = 100.0 * R["trig_games"][label] / max(1, R["games"])
            L.append(f"  {label:<44} {tot:>6} {tot/max(1,R['games']):>9.2f} {gp:>6.0f}%")

    L.append("\nNOTE: deltas are correlational, not causal. A high-delta card may just "
             "\nride along in good draws. Use `--suggest` to A/B test specific swaps.")
    return "\n".join(L)


MAX_COPIES = 4


def pick_filler(deck, cut_name):
    """Choose a card to duplicate when backfilling a cut slot.

    MUST NOT create a 5th copy. Returns (card, note) where card may be None if
    no legal filler exists (every other card is already at 4 copies), in which
    case the caller should run a 59-card deck rather than an illegal 60.

    Deterministic: prefers the card with the FEWEST current copies (topping up a
    1-of perturbs the deck's identity less than pushing a 3-of to 4); ties are
    broken by name so results are reproducible."""
    from collections import Counter
    counts = Counter(c.name for c in deck)
    eligible = [c for c in _unique_cards(deck)
                if c.name != cut_name and counts[c.name] < MAX_COPIES]
    if not eligible:
        return None, "no legal filler (all other cards at 4 copies); ran 59 cards"
    best = min(eligible, key=lambda c: (counts[c.name], c.name))
    return best, f"+1 {best.name} ({counts[best.name]}->{counts[best.name]+1})"


def _unique_cards(deck):
    seen, out = set(), []
    for c in deck:
        if c.name not in seen:
            seen.add(c.name); out.append(c)
    return out


def apply_cut(deck, cut_name):
    """Remove one copy of cut_name and backfill with a LEGAL duplicate.
    Returns (new_deck, note)."""
    out = list(deck)
    for i, c in enumerate(out):
        if c.name == cut_name:
            del out[i]
            break
    else:
        return out, "card not in deck; no-op"
    filler, note = pick_filler(out, cut_name)
    if filler is not None:
        out.append(filler)
    return out, note


def suggest_swaps(db, deckU, deckO, policyU, policyO, games, iters, seed,
                  candidates_out, label_u="Deck A"):
    """A/B test: for each flagged cut candidate, drop one copy and backfill with
    one legal extra copy of another in-deck card, then measure the win-rate
    change. This is a CUT test, not an add test.

    The backfill never creates a 5th copy of any card (see pick_filler)."""

    def winrate(deck):
        polU = make_policy(policyU, iters, seed=1)
        polO = make_policy(policyO, iters, seed=2)
        wins = 0
        for i in range(games):
            s0 = (i % 2 == 0)
            won, *_ = _run_tracked_game(deck, deckO, polU, polO, seed + i, s0)
            wins += won
        return _rate(wins, games)

    base = winrate(deckU)
    lines = [f"\nSWAP TESTING ({games} games each, base win rate {base:.0f}%)"]
    names = set(c.name for c in deckU)
    for cut in candidates_out:
        if cut not in names:
            continue
        trial, note = apply_cut(deckU, cut)
        wr = winrate(trial)
        lines.append(f"  -1 {cut:32s} -> {wr:4.0f}%  ({wr-base:+.0f})   [{note}]")
    lines.append("  Backfill duplicates a legal in-deck card (never a 5th copy).")
    lines.append("  To test real ADDITIONS, edit the decklist and re-run analyze.")
    return "\n".join(lines)
