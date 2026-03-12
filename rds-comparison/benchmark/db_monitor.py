#!/usr/bin/env python3
"""
Background DB internals sampler.
Runs during each benchmark test, captures DB stats every 5 seconds.
Outputs JSON Lines to a file for later report generation.
"""

import argparse
import json
import sys
import time
from datetime import datetime


def sample_pg(host, port, user, password, dbname, reader_host):
    import psycopg2

    def _query(conn, sql):
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows

    sample = {"ts": datetime.utcnow().isoformat(), "engine": "postgresql"}

    # Writer connection
    try:
        wconn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
        wconn.autocommit = True

        # Database stats
        rows = _query(wconn, "SELECT xact_commit, xact_rollback, tup_inserted, tup_updated, "
                              "tup_deleted, tup_fetched, blks_read, blks_hit, deadlocks, conflicts "
                              "FROM pg_stat_database WHERE datname = current_database()")
        sample["db_stats"] = rows[0] if rows else {}

        # Connection states
        rows = _query(wconn, "SELECT state, count(*) as cnt FROM pg_stat_activity "
                              "WHERE datname = current_database() GROUP BY state")
        sample["connections"] = {r["state"] or "null": int(r["cnt"]) for r in rows}

        # Background writer
        rows = _query(wconn, "SELECT checkpoints_timed, checkpoints_req, buffers_checkpoint, "
                              "buffers_clean, buffers_backend, buffers_alloc FROM pg_stat_bgwriter")
        sample["bgwriter"] = rows[0] if rows else {}

        # Table stats
        rows = _query(wconn, "SELECT n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup "
                              "FROM pg_stat_user_tables WHERE relname = 'benchmark_data'")
        sample["table_stats"] = rows[0] if rows else {}

        # Lock summary
        rows = _query(wconn, "SELECT mode, count(*) as cnt FROM pg_locks GROUP BY mode")
        sample["locks"] = {r["mode"]: int(r["cnt"]) for r in rows}

        # Buffer cache hit ratio
        rows = _query(wconn, "SELECT CASE WHEN blks_hit + blks_read > 0 "
                              "THEN round(blks_hit::numeric / (blks_hit + blks_read) * 100, 2) "
                              "ELSE 100 END as hit_ratio "
                              "FROM pg_stat_database WHERE datname = current_database()")
        sample["cache_hit_ratio"] = float(rows[0]["hit_ratio"]) if rows else 0

        wconn.close()
    except Exception as e:
        sample["writer_error"] = str(e)

    # Reader — replication lag
    try:
        rconn = psycopg2.connect(host=reader_host, port=port, user=user, password=password, dbname=dbname)
        rconn.autocommit = True
        rows = _query(rconn, "SELECT CASE WHEN pg_is_in_recovery() THEN "
                              "EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())) "
                              "ELSE 0 END as lag_sec")
        sample["replication_lag_sec"] = float(rows[0]["lag_sec"]) if rows and rows[0]["lag_sec"] else 0
        rconn.close()
    except Exception as e:
        sample["reader_error"] = str(e)

    return sample


def sample_mysql(host, port, user, password, dbname, reader_host):
    import pymysql

    sample = {"ts": datetime.utcnow().isoformat(), "engine": "mysql"}

    try:
        wconn = pymysql.connect(host=host, port=port, user=user, password=password, database=dbname)
        cur = wconn.cursor()

        # Key global status vars
        status_vars = [
            "Threads_connected", "Threads_running", "Queries",
            "Com_insert", "Com_select",
            "Innodb_rows_inserted", "Innodb_rows_read",
            "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads",
            "Innodb_buffer_pool_pages_total", "Innodb_buffer_pool_pages_free",
            "Innodb_buffer_pool_pages_dirty",
            "Innodb_data_reads", "Innodb_data_writes",
            "Innodb_log_writes", "Innodb_os_log_written",
            "Innodb_row_lock_waits", "Innodb_row_lock_time",
            "Bytes_received", "Bytes_sent",
        ]
        placeholders = ",".join(f"'{v}'" for v in status_vars)
        cur.execute(f"SHOW GLOBAL STATUS WHERE Variable_name IN ({placeholders})")
        sample["global_status"] = {row[0]: row[1] for row in cur.fetchall()}

        # Buffer pool hit ratio
        reqs = int(sample["global_status"].get("Innodb_buffer_pool_read_requests", 1))
        reads = int(sample["global_status"].get("Innodb_buffer_pool_reads", 0))
        sample["cache_hit_ratio"] = round((1 - reads / max(reqs, 1)) * 100, 2)

        # Connection states
        cur.execute("SELECT command, count(*) as cnt FROM information_schema.processlist GROUP BY command")
        sample["connections"] = {row[0]: int(row[1]) for row in cur.fetchall()}

        # InnoDB status (summary only — avoid huge output)
        cur.execute("SHOW ENGINE INNODB STATUS")
        innodb_status = cur.fetchone()
        if innodb_status:
            status_text = innodb_status[2] if len(innodb_status) > 2 else ""
            # Extract key sections
            for section in ["SEMAPHORES", "TRANSACTIONS", "LOG", "BUFFER POOL AND MEMORY"]:
                start = status_text.find(section)
                if start != -1:
                    end = status_text.find("\n---", start + 1)
                    chunk = status_text[start:end][:500] if end != -1 else status_text[start:start+500]
                    sample[f"innodb_{section.lower().replace(' ', '_')}"] = chunk

        cur.close()
        wconn.close()
    except Exception as e:
        sample["writer_error"] = str(e)

    # Reader — replication lag
    try:
        rconn = pymysql.connect(host=reader_host, port=port, user=user, password=password, database=dbname)
        rcur = rconn.cursor()
        rcur.execute("SELECT @@aurora_server_id as server_id")
        server_info = rcur.fetchone()
        # Aurora replica lag via information_schema
        rcur.execute("SELECT IF(@@read_only, 'replica', 'writer') as role")
        role = rcur.fetchone()
        sample["reader_role"] = role[0] if role else "unknown"
        # Aurora-specific lag metric (ms)
        try:
            rcur.execute("SELECT VARIABLE_VALUE FROM performance_schema.global_status "
                         "WHERE VARIABLE_NAME = 'Aurora_replica_lag_in_msec'")
            lag_row = rcur.fetchone()
            sample["replication_lag_sec"] = float(lag_row[0]) / 1000.0 if lag_row and lag_row[0] else 0
        except Exception:
            sample["replication_lag_sec"] = 0
        rcur.close()
        rconn.close()
    except Exception as e:
        sample["reader_error"] = str(e)

    return sample


def main():
    parser = argparse.ArgumentParser(description="DB Internals Monitor")
    parser.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    parser.add_argument("--host", required=True)
    parser.add_argument("--reader-host", required=True)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--dbname", default="benchmark")
    parser.add_argument("--output", required=True, help="Output JSONL file path")
    parser.add_argument("--interval", type=int, default=5, help="Sample interval in seconds")
    parser.add_argument("--duration", type=int, default=300, help="Total monitoring duration")

    args = parser.parse_args()
    if args.port is None:
        args.port = 5432 if args.engine == "postgresql" else 3306

    sampler = sample_pg if args.engine == "postgresql" else sample_mysql
    start = time.time()

    with open(args.output, "w") as f:
        while time.time() - start < args.duration:
            try:
                sample = sampler(args.host, args.port, args.user, args.password,
                                 args.dbname, args.reader_host)
                f.write(json.dumps(sample, default=str) + "\n")
                f.flush()
            except Exception as e:
                f.write(json.dumps({"ts": datetime.utcnow().isoformat(), "error": str(e)}) + "\n")
                f.flush()
            time.sleep(args.interval)

    print(f"Monitor complete: {args.output}")


if __name__ == "__main__":
    main()
