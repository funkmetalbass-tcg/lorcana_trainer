"""Combo awareness for the heuristic policy.

`greedy_policy` has no lookahead, so it cannot plan a turn-3 shift from turn 1.
It does not need to. Most of the value of a multi-turn combo is captured by not
DESTROYING the pieces -- not inking the base a shift card wants, not trading it
away in a challenge, not shipping it to the bottom in the mulligan. Those are
all local decisions about the CURRENT state, which a greedy policy can make
correctly if it knows which cards are combo-relevant.

This module answers that question. Everything here is derived from the cards
currently in a player's hand and play area, so there is no cached deck-level
graph to invalidate when the state changes (or when MCTS clones a Game). The
only cached thing is per-Card and immutable: which base names a given card can
shift onto, memoized by card name because parsing the Combo/Duo/Temporary Shift
variants costs a regex.

Cost note: this runs inside the MCTS rollout policy, i.e. millions of times.
Every public function is O(hand x board) over collections that are ~10 elements
each, with no regex on the hot path after the first sighting of a card.
"""

# card name -> frozenset of base names it may shift onto ("" key never used)
_SHIFT_TARGETS = {}


def shift_target_names(card):
    """Base names `card` may be played on top of via any Shift variant.

    Empty frozenset for non-shift cards. Covers plain Shift (base name),
    Combo Shift and Duo Shift (either/both halves of an '&' name), and
    Temporary Shift (base name).
    """
    hit = _SHIFT_TARGETS.get(card.name)
    if hit is not None:
        return hit
    if card.shift_ink is not None or abilities.temporary_shift_cost(card) is not None:
        out = frozenset((card.base_name,))
    elif (abilities.combo_shift_cost(card) is not None
          or abilities.duo_shift_cost(card) is not None):
        out = frozenset(abilities.combo_shift_names(card))
    else:
        out = frozenset()
    _SHIFT_TARGETS[card.name] = out
    return out


def is_shift_card(card):
    return bool(shift_target_names(card))


def live_shift_bases(game, p):
    """uids of p's characters that a shift card currently in p's HAND could be
    played on top of. These are assets with pending value the greedy scorer
    would otherwise not see."""
    hand_targets = set()
    for c in game.players[p].hand:
        hand_targets |= shift_target_names(c)
    if not hand_targets:
        return frozenset()
    return frozenset(ch.uid for ch in game.my_chars(p)
                     if ch.card.base_name in hand_targets)


def combo_protected_names(game, p):
    """Card names in p's hand that should not be inked away.

    Two cases:
      * a shift card whose target is already on the board or also in hand
        (inking it throws away the discount and the tempo swing);
      * a character that a shift card in hand is waiting to land on
        (inking it strands the shift card as an overcosted body).

    A shift card with no target anywhere is NOT protected -- it is a fine ink.
    """
    hand = game.players[p].hand
    board_bases = set(ch.card.base_name for ch in game.my_chars(p))

    protected = set()
    wanted = set()          # base names some in-hand shift card is looking for
    for c in hand:
        tgts = shift_target_names(c)
        if not tgts:
            continue
        wanted |= tgts
        # A plain-Shift card shares its own base name, so it must not count as
        # its own base: look for a DIFFERENT, non-shift character in hand.
        base_in_hand = any(o.is_character and o.name != c.name
                           and o.base_name in tgts and not is_shift_card(o)
                           for o in hand)
        if (tgts & board_bases) or base_in_hand:
            protected.add(c.name)
    for c in hand:
        if c.is_character and c.base_name in wanted and not is_shift_card(c):
            protected.add(c.name)
    return protected


def combo_mulligan_keeps(hand):
    """Names in an opening hand worth keeping past a naive cost cutoff.

    Deliberately hand-local: at mulligan time nothing is in play, so the only
    inference available is that a shift card and one of its bases are both
    here. That is precisely the case a flat 'bottom everything 5+' rule breaks.
    """
    keeps = set()
    for c in hand:
        tgts = shift_target_names(c)
        if not tgts:
            continue
        # A plain-Shift card shares its own base name, so a card only counts as
        # a base for `c` if it is a different card and is not itself a shift.
        bases = [o for o in hand
                 if o.is_character and o.name != c.name
                 and o.base_name in tgts and not is_shift_card(o)]
        if bases:
            keeps.add(c.name)
            keeps.update(o.name for o in bases)
    return keeps


from . import abilities  # noqa: E402
