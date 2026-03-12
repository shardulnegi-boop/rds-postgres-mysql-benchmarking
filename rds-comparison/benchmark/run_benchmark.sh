#!/bin/bash
###############################################################################
# Aurora MySQL vs PostgreSQL — 30-Minute Benchmark
# 1 Writer + 2 Readers | Metrics + Internals + Report
###############################################################################
set -euo pipefail

source /home/ec2-user/db_config.env

RESULTS="/home/ec2-user/results"
BENCH="/home/ec2-user/benchmark"
PY="python3.11"
TS=$(date +%Y%m%d_%H%M%S)

mkdir -p "$RESULTS"

DURATION=240       # 4 minutes per test
WORKERS=16
RATES=(200 400 1000)
COOLDOWN=15

BENCH_START=$(date -u +%Y-%m-%dT%H:%M:%S)

echo "╔═══════════════════════════════════════════════════════════════════╗"
echo "║  Aurora MySQL vs PostgreSQL — 30-Minute Stress Test             ║"
echo "║  Instance: db.r7g.4xlarge | 1 Writer + 2 Readers per engine    ║"
echo "║  Rates: ${RATES[*]} MB/s | Duration: ${DURATION}s per test         ║"
echo "║  Started: $(date)                               ║"
echo "╚═══════════════════════════════════════════════════════════════════╝"
echo ""

# ── Connectivity ────────────────────────────────────────
echo "Checking connectivity..."
pg_isready -h "$PG_HOST" -p 5432 -U "$DB_USER" -t 5 >/dev/null 2>&1 && echo "  PG Writer: OK" || { echo "  PG Writer: FAIL"; exit 1; }
pg_isready -h "$PG_READER_HOST" -p 5432 -U "$DB_USER" -t 5 >/dev/null 2>&1 && echo "  PG Reader: OK" || echo "  PG Reader: WAITING..."
mysql -h "$MYSQL_HOST" -u "$DB_USER" -p"$DB_PASS" -e "SELECT 1" >/dev/null 2>&1 && echo "  MySQL Writer: OK" || { echo "  MySQL Writer: FAIL"; exit 1; }
mysql -h "$MYSQL_READER_HOST" -u "$DB_USER" -p"$DB_PASS" -e "SELECT 1" >/dev/null 2>&1 && echo "  MySQL Reader: OK" || echo "  MySQL Reader: WAITING..."
echo ""

# ── Run Tests ───────────────────────────────────────────
LOGS=()

for RATE in "${RATES[@]}"; do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  TESTING AT ${RATE} MB/s"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    for ENGINE in postgresql mysql; do
        if [ "$ENGINE" = "postgresql" ]; then
            HOST="$PG_HOST"; READER="$PG_READER_HOST"; PORT=5432
        else
            HOST="$MYSQL_HOST"; READER="$MYSQL_READER_HOST"; PORT=3306
        fi

        LOG="$RESULTS/${ENGINE}_${RATE}mbps_${TS}.log"
        MON="$RESULTS/monitor_${ENGINE}_${RATE}mbps_${TS}.jsonl"

        echo ""
        echo ">>> $ENGINE @ ${RATE} MB/s (${DURATION}s)"

        # Start DB monitor in background
        $PY "$BENCH/db_monitor.py" \
            --engine "$ENGINE" \
            --host "$HOST" --reader-host "$READER" --port "$PORT" \
            --user "$DB_USER" --password "$DB_PASS" --dbname "$DB_NAME" \
            --output "$MON" --duration "$((DURATION + 10))" --interval 5 &
        MON_PID=$!

        # Run load test
        $PY "$BENCH/load_test.py" \
            --engine "$ENGINE" \
            --host "$HOST" --port "$PORT" \
            --user "$DB_USER" --password "$DB_PASS" --dbname "$DB_NAME" \
            --target-mbps "$RATE" --duration "$DURATION" --workers "$WORKERS" \
            2>&1 | tee "$LOG"

        # Stop monitor
        kill $MON_PID 2>/dev/null || true
        wait $MON_PID 2>/dev/null || true

        LOGS+=("$LOG")
        echo "Cooldown ${COOLDOWN}s..."
        sleep "$COOLDOWN"
    done
done

BENCH_END=$(date -u +%Y-%m-%dT%H:%M:%S)

# ── Console Summary ─────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════════════════════╗"
echo "║                    RESULTS COMPARISON                           ║"
echo "╚═══════════════════════════════════════════════════════════════════╝"
echo ""

for f in "${LOGS[@]}"; do
    grep "^JSON_RESULT:" "$f" 2>/dev/null | sed 's/^JSON_RESULT://'
done | $PY -c "
import sys, json
results = [json.loads(l.strip()) for l in sys.stdin if l.strip()]
if not results: sys.exit(0)
print(f\"{'Engine':<13} {'Target':>8} {'Actual':>8} {'%':>7} {'Data':>8} {'Rows':>14} {'Errors':>8} {'Status':>10}\")
print('-' * 78)
for r in sorted(results, key=lambda x: (x['target_mbps'], x['engine'])):
    print(f\"{r['engine']:<13} {r['target_mbps']:>6}  {r['actual_mbps']:>7.1f} {r.get('achieved_pct',0):>6.0f}% {r['total_gb']:>7.2f} {r['total_rows']:>14,} {r['errors']:>8,} {r['status']:>10}\")
by_rate = {}
for r in results: by_rate.setdefault(r['target_mbps'], {})[r['engine']] = r
print()
for rate in sorted(by_rate):
    e = by_rate[rate]
    if len(e) == 2:
        p, m = e.get('postgresql',{}), e.get('mysql',{})
        pw, mw = p.get('actual_mbps',0), m.get('actual_mbps',0)
        w = 'PostgreSQL' if pw > mw else 'MySQL'
        print(f'  {rate} MB/s: {w} wins  [PG={pw:.0f} {p.get(\"status\",\"?\")} | MySQL={mw:.0f} {m.get(\"status\",\"?\")}]')
"

# ── Generate HTML Report ────────────────────────────────
echo ""
echo "Generating HTML report with charts..."
$PY "$BENCH/generate_report.py" \
    --results-dir "$RESULTS" \
    --region "$AWS_REGION" \
    --start-time "$BENCH_START" \
    --end-time "$BENCH_END" \
    --output "$RESULTS/benchmark_report_${TS}.html"

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  ALL DONE!"
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "  Report:  $RESULTS/benchmark_report_${TS}.html"
echo "  Logs:    $RESULTS/"
echo ""
echo "  Download the report:"
echo "    scp -i <key> ec2-user@\$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):/home/ec2-user/results/benchmark_report_${TS}.html ."
echo ""
echo "  NEXT: Destroy infrastructure!"
echo "    bash scripts/destroy.sh"
