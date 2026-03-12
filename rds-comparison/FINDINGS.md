# Aurora MySQL vs PostgreSQL Benchmark — Findings

**Date:** 2026-03-12
**Instance:** db.r7g.4xlarge (16 vCPU, 128 GB RAM) | I/O-Optimized Storage
**Topology:** 1 Writer + 2 Readers per engine | us-east-2
**Duration:** ~25 minutes (240s per test, 3 rates, 2 engines)
**Cost:** ~$10

---

## What We Tested

| Engine               | Target Rates     | Actual Sustained | Errors | Status   |
|----------------------|------------------|------------------|--------|----------|
| Aurora PostgreSQL 16 | 200/400/1000 MB/s | ~102 MB/s        | 0      | Capped   |
| Aurora MySQL 8.0     | 200/400/1000 MB/s | ~150 MB/s        | 0      | Capped   |

Both engines plateaued at the same throughput regardless of target rate (200, 400, or 1000 MB/s gave identical results).

---

## What We Actually Learned

### 1. The experiment did NOT stress-test the databases
The single EC2 load generator (c7g.4xlarge) maxed out its CPU generating test data before either database broke a sweat. We were benchmarking the EC2 instance, not Aurora. CloudWatch metrics confirmed both Aurora writers had plenty of headroom (low CPU, low IOPS relative to capacity).

### 2. The MB/s comparison between engines is unreliable
- PostgreSQL measured **actual bytes** sent via COPY
- MySQL measured **estimated bytes** (fixed 1750 bytes/row assumption)
- Row counts were nearly identical (~22M per engine per test, ~91K rows/s)
- The ~50% MB/s gap between engines is likely a measurement artifact, not a real performance difference

### 3. Neither engine failed or threw errors
At the load levels we achieved (~100-150 MB/s), both engines handled everything cleanly. Zero errors across all 6 test runs. This tells us nothing about behavior at 300+ MB/s where our production MySQL previously broke.

### 4. We did NOT reach the failure point
The whole purpose was to find where each engine breaks. We never got there. Both engines were coasting.

---

## What This Experiment Did NOT Answer

- **Which engine handles 300 MB/s+ better?** — Unknown. We never pushed either engine past ~150 MB/s of actual load.
- **Will PostgreSQL survive where MySQL broke at 300 MB/s?** — Unknown. Not tested.
- **What happens at 1 GB/s?** — Unknown. Load generator was the bottleneck.
- **Is switching from MySQL to PostgreSQL worth it?** — Inconclusive from this data.

---

## Why This Matters for the Decision

Even if we re-run with enough load generators to actually stress the databases:

- **Aurora is single-writer.** One db.r7g.4xlarge writer tops out around 300-500 MB/s regardless of engine. PostgreSQL (via COPY) may push that ceiling slightly higher than MySQL (via INSERT), but both hit a wall.
- **At 1 GB/s, both engines will fail.** Switching MySQL to PostgreSQL buys headroom, not a solution. If we're growing toward 1 GB/s, we'll be back in the same situation.
- **The problem is architectural, not engine-level.** A single-writer relational database is not designed for 1 GB/s sustained ingestion.

---

## Recommendation

Switching Aurora MySQL to Aurora PostgreSQL is **not the fix** for sustained high-throughput ingestion. It may delay the problem (COPY is faster than INSERT), but does not solve it.

For 1 GB/s+ ingestion, evaluate one of:

| Approach | How It Works | Pros | Cons |
|----------|-------------|------|------|
| **Buffer + Drain** (Kinesis/Kafka → Aurora) | Absorb writes in a stream, drain to Aurora at sustainable rate | Minimal architecture change, keeps Aurora | Adds seconds of query delay on newest data |
| **Shard across N clusters** | Split by tenant/device/region across 4+ Aurora clusters | Stays fully relational | Operational complexity, cross-shard queries are hard |
| **ClickHouse / Redshift** for hot path | Purpose-built columnar store for analytics ingestion | Handles 1 GB/s+ easily | New technology to operate, not transactional |
| **Hybrid** | Hot writes → ClickHouse, warm data → Aurora for transactional queries | Best of both worlds | Most complex to build and maintain |

---

## To Get a Valid Engine Comparison (If Still Needed)

If the team still wants a head-to-head MySQL vs PostgreSQL comparison, the experiment needs:
1. **3-4 EC2 load generators** running in parallel against each writer
2. **Identical byte measurement** for both engines
3. **Ramp up until failure** — not fixed targets, but gradually increasing load until errors/latency spike
4. **Server-side metrics only** — rely on CloudWatch WriteThroughput, not client-side measurement
5. Estimated cost: ~$15-20, ~45 minutes
