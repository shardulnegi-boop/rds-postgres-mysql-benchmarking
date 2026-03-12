#!/bin/bash
###############################################################################
# Aurora Stress Test v2 — Ramp to Failure
# Pre-generate data → PG stress → MySQL stress → Compare
###############################################################################
set -euo pipefail

source /home/ec2-user/db_config.env

PY="python3.11"
BENCH="/home/ec2-user/benchmark"
RESULTS="/home/ec2-user/results"
TS=$(date +%Y%m%d_%H%M%S)
DATA_DIR="/data"

RAMP_STEP=4
ROUND_DURATION=60
MAX_WORKERS=64

mkdir -p "$RESULTS"

echo "╔═══════════════════════════════════════════════════════════════════╗"
echo "║  Aurora Stress Test v2 — RAMP TO FAILURE                        ║"
echo "║  Instance: db.r7g.4xlarge | 1W + 2R per engine                  ║"
echo "║  Ramp: +${RAMP_STEP} workers/round | ${ROUND_DURATION}s/round   ║"
echo "║  Started: $(date)                                               ║"
echo "╚═══════════════════════════════════════════════════════════════════╝"
echo ""

# ── Connectivity ──────────────────────────────────────────
echo "Checking connectivity..."
pg_isready -h "$PG_HOST" -p 5432 -U "$DB_USER" -t 5 >/dev/null 2>&1 && echo "  PG Writer: OK" || { echo "  PG Writer: FAIL"; exit 1; }
mysql -h "$MYSQL_HOST" -u "$DB_USER" -p"$DB_PASS" -e "SELECT 1" >/dev/null 2>&1 && echo "  MySQL Writer: OK" || { echo "  MySQL Writer: FAIL"; exit 1; }
echo ""

# ── Phase 1: Generate Data ───────────────────────────────
echo "Phase 1: Generating test data..."
if [ -f "$DATA_DIR/data_000.tsv" ]; then
    echo "  Data files already exist, skipping generation"
    ls -lh "$DATA_DIR"/*.tsv | wc -l | xargs -I{} echo "  {} files found"
else
    $PY "$BENCH/generate_data.py" \
        --output-dir "$DATA_DIR" \
        --num-files 20 \
        --file-size-mb 1024
fi
echo ""

# ── Phase 2: PostgreSQL Stress Test ──────────────────────
echo "Phase 2: PostgreSQL ramp-to-failure..."
PG_LOG="$RESULTS/pg_stress_${TS}.log"
$PY "$BENCH/stress_test.py" \
    --engine postgresql \
    --host "$PG_HOST" --port 5432 \
    --user "$DB_USER" --password "$DB_PASS" --dbname "$DB_NAME" \
    --data-dir "$DATA_DIR" \
    --ramp-step "$RAMP_STEP" \
    --round-duration "$ROUND_DURATION" \
    --max-workers "$MAX_WORKERS" \
    2>&1 | tee "$PG_LOG"

echo ""
echo "Cooldown 30s before MySQL test..."
sleep 30

# ── Phase 3: MySQL Stress Test ───────────────────────────
echo "Phase 3: MySQL ramp-to-failure..."
MYSQL_LOG="$RESULTS/mysql_stress_${TS}.log"
$PY "$BENCH/stress_test.py" \
    --engine mysql \
    --host "$MYSQL_HOST" --port 3306 \
    --user "$DB_USER" --password "$DB_PASS" --dbname "$DB_NAME" \
    --data-dir "$DATA_DIR" \
    --ramp-step "$RAMP_STEP" \
    --round-duration "$ROUND_DURATION" \
    --max-workers "$MAX_WORKERS" \
    2>&1 | tee "$MYSQL_LOG"

# ── Comparison ───────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════════════════════╗"
echo "║                    HEAD-TO-HEAD COMPARISON                      ║"
echo "╚═══════════════════════════════════════════════════════════════════╝"
echo ""

$PY -c "
import json, sys

results = {}
for f in ['$PG_LOG', '$MYSQL_LOG']:
    with open(f) as fh:
        for line in fh:
            if line.startswith('JSON_RESULT:'):
                r = json.loads(line[len('JSON_RESULT:'):])
                results[r['engine']] = r

if len(results) < 2:
    print('  ERROR: Missing results for one or both engines')
    sys.exit(1)

pg = results.get('postgresql', {})
my = results.get('mysql', {})

print(f\"{'Engine':<13} {'Peak MB/s':>10} {'Peak Workers':>13} {'Broke?':>8} {'At Workers':>11} {'At MB/s':>9}\")
print('-' * 70)
for name, r in [('PostgreSQL', pg), ('MySQL', my)]:
    broke = 'YES' if r.get('broke') else 'NO'
    bw = str(r.get('breaking_workers', '-'))
    bm = f\"{r.get('breaking_mbps', 0):.0f}\" if r.get('breaking_mbps') else '-'
    print(f\"{name:<13} {r.get('peak_mbps',0):>8.1f}  {r.get('peak_workers',0):>11}   {broke:>8} {bw:>11} {bm:>9}\")

print()
if pg.get('broke') and my.get('broke'):
    if pg['breaking_workers'] > my['breaking_workers']:
        print('  WINNER: PostgreSQL (broke later)')
    elif my['breaking_workers'] > pg['breaking_workers']:
        print('  WINNER: MySQL (broke later)')
    else:
        if pg.get('peak_mbps', 0) > my.get('peak_mbps', 0):
            print('  WINNER: PostgreSQL (higher peak throughput)')
        else:
            print('  WINNER: MySQL (higher peak throughput)')
elif pg.get('broke') and not my.get('broke'):
    print('  WINNER: MySQL (PostgreSQL broke, MySQL survived)')
elif my.get('broke') and not pg.get('broke'):
    print('  WINNER: PostgreSQL (MySQL broke, PostgreSQL survived)')
else:
    print('  NEITHER BROKE — need more workers or bigger EC2')
    if pg.get('peak_mbps', 0) > my.get('peak_mbps', 0):
        print(f\"  PostgreSQL had higher peak: {pg['peak_mbps']:.0f} vs {my['peak_mbps']:.0f} MB/s\")
    else:
        print(f\"  MySQL had higher peak: {my['peak_mbps']:.0f} vs {pg['peak_mbps']:.0f} MB/s\")

print()
print('Per-round breakdown:')
for name, r in [('PostgreSQL', pg), ('MySQL', my)]:
    print(f\"\\n  {name}:\")
    for rd in r.get('rounds', []):
        err_mark = ' <<<< BROKE' if rd['errors'] > 0 else ''
        print(f\"    Round {rd['round']:>2}: {rd['workers']:>3} workers → {rd['avg_mbps']:>8.1f} MB/s | \"
              f\"{rd['total_gb']:>6.1f} GB | {rd['errors']} errors{err_mark}\")
" 2>&1 | tee "$RESULTS/comparison_${TS}.txt"

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  DONE! Results in $RESULTS/"
echo "════════════════════════════════════════════════════════════════════"
