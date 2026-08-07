#!/usr/bin/env bash
set -euo pipefail

# Config
MY_DECK="./my_decks/deck-A_emerald_steel_ping.txt"
OPP_DIR="./opponent_decks"
OUT_DIR="./analysis-emerald_steel"
GAMES=200
ITERS=150

mkdir -p "$OUT_DIR"

# Preflight: bail before starting the batch if anything's obviously wrong.
[[ -f "$MY_DECK" ]] || { echo "Missing my-deck: $MY_DECK" >&2; exit 1; }
[[ -d "$OPP_DIR" ]] || { echo "Missing opponent dir: $OPP_DIR" >&2; exit 1; }

# Collect opponent decks first so we can report count + fail on empty.
shopt -s nullglob
opponents=( "$OPP_DIR"/*.txt )
shopt -u nullglob
(( ${#opponents[@]} )) || { echo "No .txt decks in $OPP_DIR" >&2; exit 1; }

echo "Running ${#opponents[@]} matchups at $GAMES games each..."
i=0
for opp in "${opponents[@]}"; do
  i=$((i + 1))
  # Strip path + .txt, then strip a leading "deck-" or "deck-set_13_" prefix
  # so filenames are readable. Adjust to taste.
  name="$(basename "${opp%.txt}")"
  name="${name#deck-set_13_}"
  name="${name#deck-}"
  out="$OUT_DIR/analyze-emsteel-vs-${name}.txt"

  echo "[$i/${#opponents[@]}] vs $name"
  python3 run.py \
    --deck-a "$MY_DECK" \
    --deck-b "$opp" \
    analyze \
    --a mcts \
    --b mcts \
    --games "$GAMES" \
    --iters "$ITERS" \
    --out "$out"
done

echo "Done. Results in $OUT_DIR"
