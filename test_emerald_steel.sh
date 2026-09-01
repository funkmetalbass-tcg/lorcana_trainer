#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Config (all overridable by flag; see --help)
# ---------------------------------------------------------------------------
MY_DECK="${MY_DECK:-./emsteel_champion.txt}"
OPP_DIR="${OPP_DIR:-./opponent_decks}"
OUT_DIR="${OUT_DIR:-./analysis-emerald_steel}"
GAMES="${GAMES:-200}"
ITERS="${ITERS:-150}"

detect_cpus() {
  if command -v nproc >/dev/null 2>&1; then nproc
  elif command -v sysctl >/dev/null 2>&1; then sysctl -n hw.ncpu 2>/dev/null || echo 1
  else echo 1
  fi
}

# ---------------------------------------------------------------------------
# Child mode: xargs re-invokes this script with --__run-one <idx>:<path>.
# Config arrives through the exported environment, so this block must come
# before argument parsing and before the preflight checks (already done by
# the parent -- no point paying for them once per matchup).
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--__run-one" ]]; then
  item="$2"
  idx="${item%%:*}"
  opp="${item#*:}"

  # Strip path + .txt, then a leading "deck-" or "deck-set_13_" prefix.
  name="$(basename "${opp%.txt}")"
  name="${name#deck-set_13_}"
  name="${name#deck-}"

  out="$OUT_DIR/analyze-emsteel_champion-vs-${name}.txt"
  log="$OUT_DIR/logs/${name}.log"

  echo "[$idx/${NUM_JOBS}] start  $name"
  start=$SECONDS
  # Each matchup gets its own log. Without this, the progress counters that
  # analyze_deck writes to stderr (\r-updated) from every worker would
  # overwrite each other into unreadable soup.
  if python3 run.py \
      --deck-a "$MY_DECK" \
      --deck-b "$opp" \
      analyze \
      --a mcts \
      --b mcts \
      --games "$GAMES" \
      --iters "$ITERS" \
      --out "$out" \
      >"$log" 2>&1; then
    echo "[$idx/${NUM_JOBS}] done   $name  ($((SECONDS - start))s)"
    exit 0
  else
    rc=$?
    echo "[$idx/${NUM_JOBS}] FAIL   $name  (exit $rc, see $log)" >&2
    # Leave a breadcrumb the parent can count without parsing stdout.
    : >"$OUT_DIR/logs/.failed-${name}"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Parent mode
# ---------------------------------------------------------------------------
WORKERS="$(detect_cpus)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --workers N     concurrent matchups (default: detected CPUs, here $WORKERS)
  --games N       games per matchup (default: $GAMES)
  --iters N       MCTS iterations (default: $ITERS)
  --deck PATH     deck under test (default: $MY_DECK)
  --opp-dir DIR   directory of opponent .txt decklists (default: $OPP_DIR)
  --out-dir DIR   results directory (default: $OUT_DIR)
  -h, --help      this message

Parallelism is across matchups, not within one. Each matchup is a separate
single-threaded run.py process, so --workers above your physical core count
will slow the batch down rather than speed it up.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workers)  WORKERS="$2"; shift 2 ;;
    --workers=*) WORKERS="${1#*=}"; shift ;;
    --games)    GAMES="$2"; shift 2 ;;
    --games=*)  GAMES="${1#*=}"; shift ;;
    --iters)    ITERS="$2"; shift 2 ;;
    --iters=*)  ITERS="${1#*=}"; shift ;;
    --deck)     MY_DECK="$2"; shift 2 ;;
    --deck=*)   MY_DECK="${1#*=}"; shift ;;
    --opp-dir)  OPP_DIR="$2"; shift 2 ;;
    --opp-dir=*) OPP_DIR="${1#*=}"; shift ;;
    --out-dir)  OUT_DIR="$2"; shift 2 ;;
    --out-dir=*) OUT_DIR="${1#*=}"; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

check_positive_int() {  # name value
  [[ "$2" =~ ^[1-9][0-9]*$ ]] || {
    echo "--$1 must be a positive integer (got '$2')" >&2
    exit 2
  }
}
check_positive_int workers "$WORKERS"
check_positive_int games   "$GAMES"
check_positive_int iters   "$ITERS"

# Preflight: bail before starting the batch if anything's obviously wrong.
[[ -f "$MY_DECK" ]] || { echo "Missing my-deck: $MY_DECK" >&2; exit 1; }
[[ -d "$OPP_DIR" ]] || { echo "Missing opponent dir: $OPP_DIR" >&2; exit 1; }
[[ -f "run.py"   ]] || { echo "Missing run.py in $(pwd)" >&2; exit 1; }

mkdir -p "$OUT_DIR/logs"
rm -f "$OUT_DIR"/logs/.failed-*

# Collect opponent decks first so we can report count + fail on empty.
shopt -s nullglob
opponents=( "$OPP_DIR"/*.txt )
shopt -u nullglob
(( ${#opponents[@]} )) || { echo "No .txt decks in $OPP_DIR" >&2; exit 1; }

NUM_JOBS=${#opponents[@]}
(( WORKERS > NUM_JOBS )) && WORKERS=$NUM_JOBS

export MY_DECK OUT_DIR GAMES ITERS NUM_JOBS

echo "Running $NUM_JOBS matchups at $GAMES games each, $WORKERS at a time..."
echo "Per-matchup logs: $OUT_DIR/logs/"
batch_start=$SECONDS

# Resolve our own path so the child re-invocation works regardless of how the
# script was called, and run it through `bash` so the exec bit isn't required.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

# NUL-delimited so paths with spaces survive. xargs is the worker pool: it
# keeps exactly $WORKERS children alive and starts the next as one exits, with
# no head-of-line blocking. It returns 123 if any child failed, which `set -e`
# would turn into a silent abort -- hence the `|| status=$?`.
status=0
i=0
for opp in "${opponents[@]}"; do
  i=$((i + 1))
  printf '%s\0' "$i:$opp"
done | xargs -0 -n1 -P "$WORKERS" bash "$SELF" --__run-one || status=$?

elapsed=$((SECONDS - batch_start))
shopt -s nullglob
failed=( "$OUT_DIR"/logs/.failed-* )
shopt -u nullglob

echo
if (( ${#failed[@]} )); then
  echo "Finished in ${elapsed}s with ${#failed[@]} failure(s):" >&2
  for f in "${failed[@]}"; do
    n="$(basename "$f")"; n="${n#.failed-}"
    echo "  $n  ->  $OUT_DIR/logs/${n}.log" >&2
  done
  exit 1
fi

echo "Done in ${elapsed}s. Results in $OUT_DIR"
exit $status
