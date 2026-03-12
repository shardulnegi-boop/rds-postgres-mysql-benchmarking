#!/usr/bin/env python3
"""
Ramp-to-Failure Stress Test for Aurora MySQL / PostgreSQL

Pre-generated data files are streamed into the database with increasing
parallelism until the database breaks (errors, timeouts, connection refused).

Each round adds more workers. Workers stream files via:
  - PostgreSQL: COPY FROM STDIN
  - MySQL: LOAD DATA LOCAL INFILE

The test stops when failure is detected and reports the breaking point.
"""

import argparse
import glob
import io
import json
import os
import signal
import sys
import time
from datetime import datetime
from multiprocessing import Lock, Process, Value, Array


# ──────────────────────────────────────────────────────────
# Worker: PostgreSQL (COPY FROM STDIN)
# ──────────────────────────────────────────────────────────

def pg_worker(worker_id, host, port, user, password, dbname,
              data_files, round_duration,
              shared_bytes, shared_rows, shared_errors, lock, stop_flag):
    import psycopg2

    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=dbname,
            options="-c synchronous_commit=off",
            connect_timeout=10,
        )
        conn.autocommit = True
    except Exception as e:
        print(f"  [PG Worker {worker_id}] Connection FAILED: {e}", file=sys.stderr)
        with lock:
            shared_errors.value += 1
        return

    start = time.time()
    file_idx = worker_id % len(data_files)

    try:
        while time.time() - start < round_duration and not stop_flag.value:
            data_file = data_files[file_idx % len(data_files)]
            file_idx += 1
            file_size = os.path.getsize(data_file)

            try:
                with open(data_file, "r") as f:
                    cur = conn.cursor()
                    cur.copy_expert(
                        "COPY benchmark_data (device_id, metric_name, metric_value, tags, payload) "
                        "FROM STDIN WITH (FORMAT text)",
                        f,
                    )
                    cur.close()

                row_count = file_size // 1750  # approximate
                with lock:
                    shared_bytes.value += file_size
                    shared_rows.value += row_count

            except Exception as e:
                with lock:
                    shared_errors.value += 1
                print(f"  [PG Worker {worker_id}] Error: {e}", file=sys.stderr)
                try:
                    conn.rollback()
                except Exception:
                    pass
                # If connection is dead, bail out
                try:
                    conn.cursor().execute("SELECT 1")
                except Exception:
                    print(f"  [PG Worker {worker_id}] Connection lost, exiting", file=sys.stderr)
                    return
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────
# Worker: MySQL (LOAD DATA LOCAL INFILE)
# ──────────────────────────────────────────────────────────

def mysql_worker(worker_id, host, port, user, password, dbname,
                 data_files, round_duration,
                 shared_bytes, shared_rows, shared_errors, lock, stop_flag):
    import pymysql

    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password, database=dbname,
            autocommit=True,
            local_infile=True,
            connect_timeout=10,
            read_timeout=60,
            write_timeout=60,
        )
    except Exception as e:
        print(f"  [MySQL Worker {worker_id}] Connection FAILED: {e}", file=sys.stderr)
        with lock:
            shared_errors.value += 1
        return

    start = time.time()
    file_idx = worker_id % len(data_files)

    try:
        while time.time() - start < round_duration and not stop_flag.value:
            data_file = data_files[file_idx % len(data_files)]
            file_idx += 1
            file_size = os.path.getsize(data_file)

            try:
                cur = conn.cursor()
                cur.execute(
                    f"LOAD DATA LOCAL INFILE '{data_file}' "
                    f"INTO TABLE benchmark_data "
                    f"FIELDS TERMINATED BY '\\t' "
                    f"LINES TERMINATED BY '\\n' "
                    f"(device_id, metric_name, metric_value, tags, payload)"
                )
                cur.close()

                row_count = file_size // 1750
                with lock:
                    shared_bytes.value += file_size
                    shared_rows.value += row_count

            except Exception as e:
                with lock:
                    shared_errors.value += 1
                print(f"  [MySQL Worker {worker_id}] Error: {e}", file=sys.stderr)
                try:
                    conn.rollback()
                except Exception:
                    pass
                # Check if connection is still alive
                try:
                    conn.ping(reconnect=False)
                except Exception:
                    print(f"  [MySQL Worker {worker_id}] Connection lost, exiting", file=sys.stderr)
                    return
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
            CREATE UNLOGGED TABLE benchmark_data (
                device_id    VARCHAR(64),
                metric_name  VARCHAR(128),
                metric_value DOUBLE PRECISION,
                tags         TEXT,
                payload      TEXT
            )
        """)
        # No primary key, no indexes — pure write speed
        cur.close()
        conn.close()
        print(f"  [PG] Created UNLOGGED table (no PK, no indexes) on {host}")
    else:
        import pymysql
        conn = pymysql.connect(host=host, port=port, user=user, password=password, database=dbname)
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS benchmark_data")
        cur.execute("""
            CREATE TABLE benchmark_data (
                device_id    VARCHAR(64),
                metric_name  VARCHAR(128),
                metric_value DOUBLE,
                tags         TEXT,
                payload      TEXT
            ) ENGINE=InnoDB
        """)
        # No primary key, no indexes — pure write speed
        conn.commit()
        cur.close()
        conn.close()
        print(f"  [MySQL] Created table (no PK, no indexes) on {host}")


def truncate_table(engine, host, port, user, password, dbname):
    """Fast truncate between rounds to avoid filling storage."""
    try:
        if engine == "postgresql":
            import psycopg2
            conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("TRUNCATE benchmark_data")
            cur.close()
            conn.close()
        else:
            import pymysql
            conn = pymysql.connect(host=host, port=port, user=user, password=password, database=dbname)
            cur = conn.cursor()
            cur.execute("TRUNCATE TABLE benchmark_data")
            conn.commit()
            cur.close()
            conn.close()
    except Exception as e:
        print(f"  [WARN] Truncate failed: {e}", file=sys.stderr)


# ──────────────────────────────────────────────────────────
# Ramp-to-Failure Orchestrator
# ──────────────────────────────────────────────────────────

def run_ramp_test(engine, host, port, user, password, dbname,
                  data_dir, ramp_step, round_duration, max_workers):

    data_files = sorted(glob.glob(os.path.join(data_dir, "*.tsv")))
    if not data_files:
        print(f"ERROR: No .tsv files in {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'=' * 80}")
    print(f"  {engine.upper()} RAMP-TO-FAILURE STRESS TEST")
    print(f"  Instance: db.r7g.4xlarge | Ramp: +{ramp_step} workers/round | {round_duration}s/round")
    print(f"  Data files: {len(data_files)} x {os.path.getsize(data_files[0]) / (1024**2):.0f} MB")
    print(f"{'=' * 80}")

    create_table(engine, host, port, user, password, dbname)

    worker_fn = pg_worker if engine == "postgresql" else mysql_worker
    rounds = []
    broke = False
    failure_reason = None

    num_workers = ramp_step
    while num_workers <= max_workers:
        print(f"\n--- Round {len(rounds)+1}: {num_workers} workers ---")

        # Truncate to avoid filling storage
        truncate_table(engine, host, port, user, password, dbname)
        time.sleep(1)

        shared_bytes = Value("d", 0.0)
        shared_rows = Value("l", 0)
        shared_errors = Value("l", 0)
        stop_flag = Value("i", 0)
        lock = Lock()

        workers = []
        for i in range(num_workers):
            p = Process(
                target=worker_fn,
                args=(i, host, port, user, password, dbname,
                      data_files, round_duration,
                      shared_bytes, shared_rows, shared_errors, lock, stop_flag),
            )
            workers.append(p)

        t0 = time.time()
        for w in workers:
            w.start()

        # Monitor during round
        prev_bytes = 0
        while time.time() - t0 < round_duration + 5:
            time.sleep(5)
            elapsed = time.time() - t0
            cur_bytes = shared_bytes.value
            cur_errors = shared_errors.value
            instant_mbps = (cur_bytes - prev_bytes) / (1024 * 1024) / 5
            avg_mbps = cur_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0
            print(f"  {elapsed:5.0f}s | {instant_mbps:8.1f} MB/s instant | {avg_mbps:8.1f} MB/s avg | "
                  f"errors: {cur_errors}")
            prev_bytes = cur_bytes

            if cur_errors > 10:
                print(f"  >> ERROR THRESHOLD HIT ({cur_errors} errors), stopping round")
                stop_flag.value = 1
                break

            if elapsed > round_duration:
                break

        stop_flag.value = 1
        for w in workers:
            w.join(timeout=15)
        for w in workers:
            if w.is_alive():
                w.terminate()

        elapsed = time.time() - t0
        final_bytes = shared_bytes.value
        final_rows = shared_rows.value
        final_errors = shared_errors.value
        avg_mbps = final_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0

        round_result = {
            "round": len(rounds) + 1,
            "workers": num_workers,
            "avg_mbps": round(avg_mbps, 1),
            "total_gb": round(final_bytes / (1024 ** 3), 2),
            "total_rows": final_rows,
            "errors": final_errors,
            "duration_sec": round(elapsed, 1),
        }
        rounds.append(round_result)

        print(f"\n  Result: {avg_mbps:.1f} MB/s | {final_rows:,} rows | "
              f"{final_bytes / (1024**3):.2f} GB | {final_errors} errors")

        # Check if broken
        if final_errors > 0:
            broke = True
            failure_reason = f"{final_errors} errors at {num_workers} workers ({avg_mbps:.0f} MB/s)"
            print(f"\n  >>> DATABASE BROKE: {failure_reason}")
            break

        # Check if throughput is plateauing (previous round achieved similar or higher)
        if len(rounds) >= 2:
            prev_mbps = rounds[-2]["avg_mbps"]
            if avg_mbps < prev_mbps * 0.95 and num_workers > ramp_step * 2:
                # Throughput dropped — might be saturated
                print(f"  >> Throughput dropped ({avg_mbps:.0f} vs {prev_mbps:.0f}), "
                      f"possible saturation")

        num_workers += ramp_step

    # ── Final Summary ──────────────────────────────────────
    peak_round = max(rounds, key=lambda r: r["avg_mbps"]) if rounds else {}
    result = {
        "engine": engine,
        "broke": broke,
        "failure_reason": failure_reason,
        "peak_mbps": peak_round.get("avg_mbps", 0),
        "peak_workers": peak_round.get("workers", 0),
        "breaking_workers": rounds[-1]["workers"] if broke else None,
        "breaking_mbps": rounds[-1]["avg_mbps"] if broke else None,
        "rounds": rounds,
        "timestamp": datetime.now().isoformat(),
    }

    print(f"\n{'=' * 80}")
    print(f"  {engine.upper()} STRESS TEST SUMMARY")
    print(f"{'=' * 80}")
    if broke:
        print(f"  BROKE AT:     {rounds[-1]['workers']} workers, {rounds[-1]['avg_mbps']:.1f} MB/s")
        print(f"  REASON:       {failure_reason}")
    else:
        print(f"  DID NOT BREAK (reached {max_workers} workers)")
    print(f"  PEAK:         {peak_round.get('avg_mbps', 0):.1f} MB/s at {peak_round.get('workers', 0)} workers")
    print(f"  ROUNDS:       {len(rounds)}")
    print()

    # Print per-round summary
    print(f"  {'Round':>5} | {'Workers':>8} | {'MB/s':>10} | {'Data GB':>8} | {'Rows':>14} | {'Errors':>7}")
    print(f"  {'-'*65}")
    for r in rounds:
        print(f"  {r['round']:>5} | {r['workers']:>8} | {r['avg_mbps']:>8.1f}  | "
              f"{r['total_gb']:>7.2f} | {r['total_rows']:>14,} | {r['errors']:>7}")

    print(f"\nJSON_RESULT:{json.dumps(result)}")
    return result


# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ramp-to-Failure Aurora Stress Test")
    parser.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--dbname", default="benchmark")
    parser.add_argument("--data-dir", default="/data", help="Directory with pre-generated .tsv files")
    parser.add_argument("--ramp-step", type=int, default=4, help="Workers to add each round")
    parser.add_argument("--round-duration", type=int, default=60, help="Seconds per round")
    parser.add_argument("--max-workers", type=int, default=64, help="Max workers before giving up")

    args = parser.parse_args()
    if args.port is None:
        args.port = 5432 if args.engine == "postgresql" else 3306

    run_ramp_test(
        engine=args.engine,
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        dbname=args.dbname,
        data_dir=args.data_dir,
        ramp_step=args.ramp_step,
        round_duration=args.round_duration,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    main()
