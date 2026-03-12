#!/usr/bin/env python3
"""
Aurora MySQL vs PostgreSQL — Stress Test Load Generator

Generates and inserts data at target MB/s rates using:
  - PostgreSQL: COPY (fastest bulk ingestion)
  - MySQL: batch INSERT with executemany

Usage:
  python3.11 load_test.py --engine postgresql --host <host> --user admin --password <pw> \
    --target-mbps 200 --duration 480 --workers 16

  python3.11 load_test.py --engine mysql --host <host> --user admin --password <pw> \
    --target-mbps 200 --duration 480 --workers 16
"""

import argparse
import io
import json
import os
import random
import string
import sys
import time
from datetime import datetime
from multiprocessing import Lock, Process, Value

# ──────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────

# Each row ≈ 1.75 KB:
#   device_id(64B) + metric_name(128B) + metric_value(8B) + tags(~512B) + payload(1024B)
ROW_SIZE_BYTES = 1750
BATCH_SIZE = 5000  # rows per batch insert/copy

# Pre-generate pools to avoid per-row string alloc overhead
DEVICE_IDS = [f"device-{i:06d}" for i in range(100_000)]
METRIC_NAMES = [
    f"metric.{cat}.{sub}"
    for cat in ["cpu", "mem", "disk", "net", "gpu", "io", "cache", "queue"]
    for sub in ["usage", "latency", "throughput", "errors", "saturation", "p99", "count"]
]
TAG_TEMPLATES = [
    json.dumps({
        "env": env, "region": reg, "host": f"host-{h:04d}",
        "service": svc, "version": f"v{v}.{mi}.{p}"
    })
    for env in ["prod", "staging", "dev"]
    for reg in ["us-east-1", "us-west-2", "eu-west-1"]
    for svc in ["api", "web", "worker"]
    for h in range(1, 6)
    for v in range(1, 3) for mi in range(0, 3) for p in range(0, 5)
]
PAYLOAD_CHARS = string.ascii_letters + string.digits


# ──────────────────────────────────────────────────────────
# Data Generation
# ──────────────────────────────────────────────────────────

def generate_batch_csv(batch_size):
    """Generate CSV batch for PostgreSQL COPY (tab-delimited)."""
    buf = io.StringIO()
    for _ in range(batch_size):
        device_id = random.choice(DEVICE_IDS)
        metric = random.choice(METRIC_NAMES)
        value = random.uniform(0, 10000)
        tags = random.choice(TAG_TEMPLATES)
        payload = "".join(random.choices(PAYLOAD_CHARS, k=1024))
        buf.write(f"{device_id}\t{metric}\t{value}\t{tags}\t{payload}\n")
    return buf.getvalue()


def generate_batch_tuples(batch_size):
    """Generate row tuples for MySQL executemany."""
    rows = []
    for _ in range(batch_size):
        rows.append((
            random.choice(DEVICE_IDS),
            random.choice(METRIC_NAMES),
            random.uniform(0, 10000),
            random.choice(TAG_TEMPLATES),
            "".join(random.choices(PAYLOAD_CHARS, k=1024)),
        ))
    return rows


# ──────────────────────────────────────────────────────────
# PostgreSQL Worker (COPY-based)
# ──────────────────────────────────────────────────────────

def pg_worker(worker_id, host, port, user, password, dbname,
              target_mbps_per_worker, duration_sec,
              shared_bytes, shared_rows, shared_errors, lock, stop_flag):
    import psycopg2

    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=dbname,
            options="-c synchronous_commit=off"
        )
        conn.autocommit = True
    except Exception as e:
        print(f"  [PG Worker {worker_id}] Connection failed: {e}", file=sys.stderr)
        with lock:
            shared_errors.value += 1
        return

    target_bps = target_mbps_per_worker * 1024 * 1024
    start = time.time()
    local_bytes = 0
    local_rows = 0
    local_errors = 0

    try:
        while time.time() - start < duration_sec and not stop_flag.value:
            batch_t0 = time.time()
            csv_data = generate_batch_csv(BATCH_SIZE)
            csv_bytes = len(csv_data.encode("utf-8"))

            try:
                cur = conn.cursor()
                cur.copy_expert(
                    "COPY benchmark_data (device_id, metric_name, metric_value, tags, payload) "
                    "FROM STDIN WITH (FORMAT text)",
                    io.StringIO(csv_data),
                )
                cur.close()
                with lock:
                    shared_bytes.value += csv_bytes
                    shared_rows.value += BATCH_SIZE
            except Exception as e:
                with lock:
                    shared_errors.value += 1
                if local_errors <= 5 or local_errors % 100 == 0:
                    print(f"  [PG Worker {worker_id}] Error #{local_errors}: {e}", file=sys.stderr)
                local_errors += 1
                try:
                    conn.rollback()
                except Exception:
                    pass

            # Rate-limit to target throughput
            elapsed = time.time() - batch_t0
            expected = csv_bytes / target_bps if target_bps > 0 else 0
            if elapsed < expected:
                time.sleep(expected - elapsed)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────
# MySQL Worker (batch INSERT)
# ──────────────────────────────────────────────────────────

def mysql_worker(worker_id, host, port, user, password, dbname,
                 target_mbps_per_worker, duration_sec,
                 shared_bytes, shared_rows, shared_errors, lock, stop_flag):
    import pymysql

    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password, database=dbname,
            autocommit=False,
            local_infile=True,
        )
    except Exception as e:
        print(f"  [MySQL Worker {worker_id}] Connection failed: {e}", file=sys.stderr)
        with lock:
            shared_errors.value += 1
        return

    INSERT_SQL = (
        "INSERT INTO benchmark_data "
        "(device_id, metric_name, metric_value, tags, payload) "
        "VALUES (%s, %s, %s, %s, %s)"
    )

    target_bps = target_mbps_per_worker * 1024 * 1024
    start = time.time()
    local_bytes = 0
    local_rows = 0
    local_errors = 0

    try:
        while time.time() - start < duration_sec and not stop_flag.value:
            batch_t0 = time.time()
            batch = generate_batch_tuples(BATCH_SIZE)
            batch_bytes = BATCH_SIZE * ROW_SIZE_BYTES

            try:
                cur = conn.cursor()
                cur.executemany(INSERT_SQL, batch)
                conn.commit()
                cur.close()
                with lock:
                    shared_bytes.value += batch_bytes
                    shared_rows.value += BATCH_SIZE
            except Exception as e:
                with lock:
                    shared_errors.value += 1
                if local_errors <= 5 or local_errors % 100 == 0:
                    print(f"  [MySQL Worker {worker_id}] Error #{local_errors}: {e}", file=sys.stderr)
                local_errors += 1
                try:
                    conn.rollback()
                except Exception:
                    pass

            # Rate-limit
            elapsed = time.time() - batch_t0
            expected = batch_bytes / target_bps if target_bps > 0 else 0
            if elapsed < expected:
                time.sleep(expected - elapsed)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────
# Real-time Monitor
# ──────────────────────────────────────────────────────────

def monitor(engine, target_mbps, duration_sec,
            shared_bytes, shared_rows, shared_errors, stop_flag):
    start = time.time()
    prev_bytes = 0
    prev_rows = 0

    print(f"\n{'=' * 90}")
    print(f"  {engine.upper()} BENCHMARK  |  Target: {target_mbps} MB/s  |  Duration: {duration_sec}s")
    print(f"{'=' * 90}")
    header = (
        f"{'Time':>6} | {'Instant MB/s':>13} | {'Avg MB/s':>10} | "
        f"{'Rows/s':>10} | {'Total Rows':>13} | {'Total GB':>9} | {'Errors':>7}"
    )
    print(header)
    print("-" * len(header))

    while time.time() - start < duration_sec + 3 and not stop_flag.value:
        time.sleep(1)
        elapsed = time.time() - start
        cur_bytes = shared_bytes.value
        cur_rows = shared_rows.value
        cur_errors = shared_errors.value

        instant_mbps = (cur_bytes - prev_bytes) / (1024 * 1024)
        avg_mbps = cur_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0
        instant_rows = cur_rows - prev_rows

        print(
            f"{elapsed:5.0f}s | {instant_mbps:10.1f} MB/s | {avg_mbps:7.1f} MB/s | "
            f"{instant_rows:>10,} | {cur_rows:>13,} | {cur_bytes / (1024**3):>7.2f} GB | "
            f"{cur_errors:>7,}"
        )

        prev_bytes = cur_bytes
        prev_rows = cur_rows

    # Final summary
    elapsed = time.time() - start
    final_bytes = shared_bytes.value
    final_rows = shared_rows.value
    final_errors = shared_errors.value
    avg_mbps = final_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0
    pct = avg_mbps / target_mbps * 100 if target_mbps > 0 else 0

    if pct >= 80:
        status = "PASS"
    elif pct >= 50:
        status = "DEGRADED"
    else:
        status = "FAIL"

    print(f"\n--- {engine.upper()} SUMMARY ---")
    print(f"  Target:       {target_mbps} MB/s")
    print(f"  Actual Avg:   {avg_mbps:.1f} MB/s  ({pct:.0f}% of target)")
    print(f"  Total Data:   {final_bytes / (1024**3):.2f} GB")
    print(f"  Total Rows:   {final_rows:,}")
    print(f"  Errors:       {final_errors:,}")
    print(f"  Duration:     {elapsed:.1f}s")
    print(f"  Status:       {status}")
    print()


# ──────────────────────────────────────────────────────────
# Table Setup
# ──────────────────────────────────────────────────────────

def create_table(engine, host, port, user, password, dbname):
    if engine == "postgresql":
        import psycopg2
        conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS benchmark_data")
        cur.execute("""
            CREATE TABLE benchmark_data (
                id         BIGSERIAL PRIMARY KEY,
                ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                device_id  VARCHAR(64),
                metric_name VARCHAR(128),
                metric_value DOUBLE PRECISION,
                tags       TEXT,
                payload    TEXT
            )
        """)
        # Disable autovacuum during benchmark for max write speed
        cur.execute("ALTER TABLE benchmark_data SET (autovacuum_enabled = false)")
        cur.close()
        conn.close()
        print(f"[PostgreSQL] Created benchmark_data on {host}")
    else:
        import pymysql
        conn = pymysql.connect(host=host, port=port, user=user, password=password, database=dbname)
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS benchmark_data")
        cur.execute("""
            CREATE TABLE benchmark_data (
                id           BIGINT AUTO_INCREMENT PRIMARY KEY,
                ts           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                device_id    VARCHAR(64),
                metric_name  VARCHAR(128),
                metric_value DOUBLE,
                tags         TEXT,
                payload      TEXT
            ) ENGINE=InnoDB
        """)
        conn.commit()
        cur.close()
        conn.close()
        print(f"[MySQL] Created benchmark_data on {host}")


# ──────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────

def run_benchmark(engine, host, port, user, password, dbname,
                  target_mbps, duration_sec, num_workers):
    print(f"\n[{datetime.now().isoformat()}] Starting {engine} benchmark at {target_mbps} MB/s "
          f"with {num_workers} workers for {duration_sec}s")

    create_table(engine, host, port, user, password, dbname)
    time.sleep(2)

    shared_bytes = Value("d", 0.0)
    shared_rows = Value("l", 0)
    shared_errors = Value("l", 0)
    stop_flag = Value("i", 0)
    lock = Lock()

    target_per_worker = target_mbps / num_workers
    worker_fn = pg_worker if engine == "postgresql" else mysql_worker

    workers = []
    for i in range(num_workers):
        p = Process(
            target=worker_fn,
            args=(i, host, port, user, password, dbname,
                  target_per_worker, duration_sec,
                  shared_bytes, shared_rows, shared_errors, lock, stop_flag),
        )
        workers.append(p)

    monitor_proc = Process(
        target=monitor,
        args=(engine, target_mbps, duration_sec,
              shared_bytes, shared_rows, shared_errors, stop_flag),
    )

    # Launch
    for w in workers:
        w.start()
    monitor_proc.start()

    # Wait for workers to finish
    for w in workers:
        w.join()

    stop_flag.value = 1
    monitor_proc.join(timeout=10)
    if monitor_proc.is_alive():
        monitor_proc.terminate()

    # Collect result
    final_bytes = shared_bytes.value
    avg_mbps = final_bytes / (1024 * 1024) / duration_sec if duration_sec > 0 else 0
    pct = avg_mbps / target_mbps * 100 if target_mbps > 0 else 0

    result = {
        "engine": engine,
        "target_mbps": target_mbps,
        "actual_mbps": round(avg_mbps, 1),
        "achieved_pct": round(pct, 1),
        "total_gb": round(final_bytes / (1024 ** 3), 2),
        "total_rows": shared_rows.value,
        "errors": shared_errors.value,
        "duration_sec": duration_sec,
        "workers": num_workers,
        "status": "PASS" if pct >= 80 else "DEGRADED" if pct >= 50 else "FAIL",
        "timestamp": datetime.now().isoformat(),
    }

    # Print JSON for collection by orchestrator
    print(f"\nJSON_RESULT:{json.dumps(result)}")
    return result


# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Aurora MySQL vs PostgreSQL Stress Test Load Generator"
    )
    parser.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=None,
                        help="DB port (default: 5432 for PG, 3306 for MySQL)")
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--dbname", default="benchmark")
    parser.add_argument("--target-mbps", type=int, required=True,
                        help="Target ingestion rate in MB/s")
    parser.add_argument("--duration", type=int, default=480,
                        help="Test duration in seconds (default: 480 = 8 min)")
    parser.add_argument("--workers", type=int, default=16,
                        help="Number of parallel workers (default: 16)")

    args = parser.parse_args()
    if args.port is None:
        args.port = 5432 if args.engine == "postgresql" else 3306

    run_benchmark(
        engine=args.engine,
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        dbname=args.dbname,
        target_mbps=args.target_mbps,
        duration_sec=args.duration,
        num_workers=args.workers,
    )


if __name__ == "__main__":
    main()
