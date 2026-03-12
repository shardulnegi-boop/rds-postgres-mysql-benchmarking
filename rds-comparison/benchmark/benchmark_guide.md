# Aurora Benchmark — What to Look For

Quick reference for interpreting your 30-minute stress test results.

---

## 1. CloudWatch Metrics — Where to Check

| Metric | What It Tells You | Red Flag |
|--------|-------------------|----------|
| **CPUUtilization** | How hard the engine is working | > 80% sustained = compute bottleneck |
| **FreeableMemory** | RAM left for buffer pool/shared_buffers | < 2 GB = swapping risk, expect latency spike |
| **WriteIOPS** | Actual I/O operations hitting storage | Plateau = hit storage throughput ceiling |
| **WriteThroughput** | Bytes/sec to Aurora storage layer | Flatline below target = storage bottleneck |
| **WriteLatency** | Time per write I/O operation | > 4ms = Aurora quorum writes slowing down |
| **DiskQueueDepth** | Pending I/O requests | > 10 = requests backing up, storage can't keep pace |
| **BufferCacheHitRatio** | % of reads served from memory | < 99% = reads hitting disk, buffer pool too small |
| **NetworkReceiveThroughput** | Data arriving at the instance | Compare to instance network limit (15 Gbps for r7g.4xl) |
| **DatabaseConnections** | Active connections | Near max = connection pool exhaustion |
| **SwapUsage** | Memory swapped to disk | > 0 = serious memory pressure |
| **AuroraReplicaLag** | How far readers are behind writer | > 100ms = read-after-write inconsistency |

## 2. Where Each Engine Typically Breaks

### PostgreSQL
- **First bottleneck**: Usually CPU (WAL processing is single-threaded)
- **Second**: Memory pressure from large `shared_buffers` + write amplification
- **What to watch**: `pg_stat_bgwriter` → if `buffers_backend` spikes, background writer can't keep up
- **Replication**: WAL-based, generally low lag but CPU-bound on replay

### MySQL (InnoDB)
- **First bottleneck**: Usually redo log throughput (InnoDB double-write buffer)
- **Second**: Buffer pool dirty page flushing
- **What to watch**: `Innodb_buffer_pool_pages_dirty` — if it hits `innodb_max_dirty_pages_pct`, flushing stalls writes
- **Replication**: Binlog-based, can lag under heavy write if apply thread is slow

## 3. DB Internals — What the Monitor Captures

### PostgreSQL Internals
| Query | What It Shows |
|-------|--------------|
| `pg_stat_database` | Transaction commits/rollbacks, tuples inserted, cache hit ratio |
| `pg_stat_bgwriter` | Checkpoint frequency, buffer flushes (tells you if WAL is overwhelmed) |
| `pg_stat_user_tables` | Live vs dead tuples (dead tuples = VACUUM pressure) |
| `pg_stat_activity` | Connection states (active, idle, idle-in-transaction) |
| `pg_locks` | Lock types and counts (watch for AccessExclusiveLock) |

### MySQL Internals
| Query | What It Shows |
|-------|--------------|
| `SHOW GLOBAL STATUS` | InnoDB rows inserted, buffer pool hits/misses, lock waits |
| `SHOW ENGINE INNODB STATUS` | Transaction log, buffer pool, semaphore waits |
| `information_schema.processlist` | Thread states (Sending data, Writing to net, etc.) |
| `performance_schema.global_status` | Aurora-specific replica lag in milliseconds |

## 4. How to Read the HTML Report

The generated `benchmark_report.html` contains:

1. **Summary Cards** — Quick pass/fail per engine per rate
2. **Throughput Bar Chart** — Target vs actual (red dashed line = target)
3. **CloudWatch Timeline** — Side-by-side CPU, memory, IOPS, latency over the full test
4. **DB Internals Charts** — Cache hit ratio and replication lag from direct DB sampling
5. **Interpretation Guide** — Inline reference for what each metric means

## 5. Decision Framework

After the test, use this:

```
IF both engines PASS at your target rate:
  → Choose based on features (PostgreSQL for JSONB/extensions, MySQL for simplicity)

IF one engine FAILs at your target rate:
  → Clear winner. Pick the one that survived.

IF both DEGRADE at the same rate:
  → Scale up instance (db.r7g.8xlarge) or optimize (partitioning, batch size)

IF replication lag > 1s on readers:
  → Consider reducing write batch size or adding more readers
```

## 6. What to Check on AWS Console (Optional)

If you want to dig deeper during the test:

1. **Performance Insights** (enabled by default on these instances):
   - RDS → Performance Insights → Select instance
   - Shows: Top SQL, Wait Events, DB Load breakdown
   - Look for: Lock waits, I/O waits, CPU waits

2. **Enhanced Monitoring** (15-second granularity):
   - RDS → Monitoring → Enhanced Monitoring tab
   - Shows: OS-level CPU per core, memory breakdown, file system stats
   - Look for: Individual CPU core saturation (single-threaded bottleneck)

3. **CloudWatch Alarms** (not set up, but you could):
   - CPU > 90%, WriteLatency > 10ms, ReplicaLag > 500ms
