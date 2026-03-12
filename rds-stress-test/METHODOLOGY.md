# Aurora Stress Test — What We Did (Step by Step)

This document explains exactly what was built, deployed, and executed to stress test Aurora MySQL vs Aurora PostgreSQL.

---

## Goal

Find out which Aurora engine breaks first under sustained heavy bulk writes. Ramp up concurrent writers until one or both engines fail (errors, timeouts, stalls, crashes).

---

## What We Built

### 1. AWS Infrastructure (Terraform)

Everything was provisioned as code using Terraform in `us-east-2` (Ohio). One command to create, one command to destroy.

**27 AWS resources total:**

```
┌──────────────────────────────────────────────────────────────────┐
│  VPC (10.0.0.0/16)                                               │
│                                                                   │
│  ┌─────────────────────┐   ┌─────────────────────┐              │
│  │ Public Subnet A      │   │ Public Subnet B      │              │
│  │ (10.0.1.0/24)       │   │ (10.0.2.0/24)       │              │
│  │ us-east-2a          │   │ us-east-2b          │              │
│  │                     │   │                     │              │
│  │  ┌───────────────┐  │   │                     │              │
│  │  │ EC2 Load Gen  │  │   │                     │              │
│  │  │ c7g.4xlarge   │  │   │                     │              │
│  │  │ 16 vCPU ARM   │  │   │                     │              │
│  │  │ 100GB gp3 EBS │  │   │                     │              │
│  │  └───────────────┘  │   │                     │              │
│  └─────────────────────┘   └─────────────────────┘              │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Aurora PostgreSQL 16.11 Cluster                             │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │ │
│  │  │ Writer       │  │ Reader 1     │  │ Reader 2     │      │ │
│  │  │ db.r7g.4xl   │  │ db.r7g.4xl   │  │ db.r7g.4xl   │      │ │
│  │  │ 16vCPU/128GB │  │ 16vCPU/128GB │  │ 16vCPU/128GB │      │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘      │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Aurora MySQL 8.0 (3.08.0) Cluster                           │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │ │
│  │  │ Writer       │  │ Reader 1     │  │ Reader 2     │      │ │
│  │  │ db.r7g.4xl   │  │ db.r7g.4xl   │  │ db.r7g.4xl   │      │ │
│  │  │ 16vCPU/128GB │  │ 16vCPU/128GB │  │ 16vCPU/128GB │      │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘      │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  Internet Gateway ──► Route Table ──► Both Subnets                │
│  Security Groups: DB open on 3306/5432, EC2 open on 22            │
└──────────────────────────────────────────────────────────────────┘
```

**Specs for each component:**

| Component | Type | Specs | Why |
|-----------|------|-------|-----|
| Aurora PG Writer | db.r7g.4xlarge | 16 vCPU, 128 GB RAM, Graviton3 | Production-class instance for write-heavy workloads |
| Aurora PG Reader x2 | db.r7g.4xlarge | Same as writer | Standard 1W+2R topology matching your production setup |
| Aurora MySQL Writer | db.r7g.4xlarge | Same specs | Identical hardware for fair comparison |
| Aurora MySQL Reader x2 | db.r7g.4xlarge | Same specs | Same topology as PG cluster |
| EC2 Load Generator | c7g.4xlarge | 16 vCPU ARM, 32 GB RAM | Compute-optimized for running parallel load workers |
| EC2 EBS Volume | gp3 | 100 GB, 500 MB/s throughput, 3000 IOPS | Fast enough to read pre-generated data without disk bottleneck |

**Aurora configuration:**
- **Storage:** I/O-Optimized (`aurora-iopt1`) — no per-IOPS charges, better for write-heavy loads
- **Publicly accessible:** Yes (for easy debugging, destroyed after test)
- **Enhanced Monitoring:** 5-second intervals
- **Performance Insights:** Enabled
- **Backup:** 1-day retention, skip final snapshot (throwaway test)

---

### 2. EC2 Bootstrap (`user-data.sh`)

When the EC2 instance launched, it automatically installed:

```
dnf install python3.11, python3.11-pip, postgresql16, mariadb105, jq, htop, sysstat
pip install psycopg2-binary, pymysql, boto3
```

It also created `/home/ec2-user/db_config.env` with all the Aurora connection details (hosts, credentials, region) injected by Terraform at deploy time.

---

### 3. Data Generation Script (`generate_data.py`)

**Problem from v1:** In the first experiment, Python workers generated data on-the-fly and maxed out the EC2 CPU at ~100 MB/s. The databases were never stressed.

**Solution:** Pre-generate all test data to disk first, then stream it from disk to the database. This removes Python CPU as the bottleneck.

**What it generates:**

Each file is a tab-delimited TSV with this schema:

```
device_id       metric_name          metric_value    tags                           payload
device-042371   metric.cpu.usage     3847.291        {"env":"prod","region":...}    aB3kF9m2...  (1024 chars)
```

- **20 files**, each **1 GB** = **20 GB total** on the EC2's EBS disk
- Each row is **~1,750 bytes** (mostly the 1024-char random payload)
- ~571,000 rows per file, ~11.4 million rows total
- Device IDs drawn from a pool of 100,000 unique devices
- 56 metric types (8 categories × 7 subtypes)
- Tags are realistic JSON blobs with env/region/host/service/version
- Payload is random alphanumeric — simulates raw event data

**Why tab-delimited:** Both PostgreSQL's `COPY` and MySQL's `LOAD DATA LOCAL INFILE` natively support tab-delimited text. Same files work for both engines — no measurement bias.

**Generation speed:** ~100 MB/s on the c7g.4xlarge, took ~3-4 minutes for all 20 files.

---

### 4. Stress Test Script (`stress_test.py`)

This is the core test. It ramps up parallel workers until the database breaks.

#### How It Works

```
                    ┌─────────────────────────────────┐
                    │      Orchestrator (main)         │
                    │                                  │
                    │  Round 1: spawn 4 workers        │
                    │  Round 2: spawn 8 workers        │
                    │  Round 3: spawn 12 workers       │
                    │  ...                             │
                    │  Round N: spawn N×4 workers      │
                    │                                  │
                    │  Stop when:                      │
                    │   - errors > 10                  │
                    │   - max workers reached (64)     │
                    │   - connection lost               │
                    └──────────┬──────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐    ┌──────────┐
        │ Worker 0 │    │ Worker 1 │    │ Worker N │
        │          │    │          │    │          │
        │ Open DB  │    │ Open DB  │    │ Open DB  │
        │ conn     │    │ conn     │    │ conn     │
        │          │    │          │    │          │
        │ Loop:    │    │ Loop:    │    │ Loop:    │
        │  Pick    │    │  Pick    │    │  Pick    │
        │  next    │    │  next    │    │  next    │
        │  1GB     │    │  1GB     │    │  1GB     │
        │  file    │    │  file    │    │  file    │
        │          │    │          │    │          │
        │  COPY/   │    │  COPY/   │    │  COPY/   │
        │  LOAD    │    │  LOAD    │    │  LOAD    │
        │  DATA    │    │  DATA    │    │  DATA    │
        │          │    │          │    │          │
        │  Update  │    │  Update  │    │  Update  │
        │  shared  │    │  shared  │    │  shared  │
        │  counter │    │  counter │    │  counter │
        └──────────┘    └──────────┘    └──────────┘
              │                │                │
              ▼                ▼                ▼
        ┌─────────────────────────────────────────┐
        │            Aurora Writer Instance         │
        └─────────────────────────────────────────┘
```

#### Per Round:

1. **Truncate the table** — clears data from previous round to avoid filling storage
2. **Spawn N worker processes** using Python `multiprocessing`
3. Each worker:
   - Opens a direct DB connection
   - Picks a pre-generated 1 GB TSV file
   - Streams the entire file into the database:
     - **PostgreSQL:** `COPY benchmark_data FROM STDIN WITH (FORMAT text)` via `psycopg2.copy_expert()`
     - **MySQL:** `LOAD DATA LOCAL INFILE '/data/data_003.tsv' INTO TABLE benchmark_data` via `pymysql`
   - Updates shared memory counters (bytes sent, rows inserted, errors)
   - Picks the next file and repeats until the 60-second round ends
4. **Orchestrator monitors** every 5 seconds: prints instant/avg throughput and error count
5. If errors > 10, stops the round early
6. After 60 seconds, all workers are stopped, round results are recorded
7. **Add 4 more workers**, repeat

#### Table Schema (identical for both engines):

```sql
-- PostgreSQL
CREATE UNLOGGED TABLE benchmark_data (
    device_id    VARCHAR(64),
    metric_name  VARCHAR(128),
    metric_value DOUBLE PRECISION,
    tags         TEXT,
    payload      TEXT
)
-- No primary key, no indexes

-- MySQL
CREATE TABLE benchmark_data (
    device_id    VARCHAR(64),
    metric_name  VARCHAR(128),
    metric_value DOUBLE,
    tags         TEXT,
    payload      TEXT
) ENGINE=InnoDB
-- No primary key, no indexes
```

**Key difference:** PostgreSQL table is `UNLOGGED` (no Write-Ahead Log). MySQL/InnoDB always writes redo logs — there is no equivalent of UNLOGGED in Aurora MySQL.

#### Shared Memory (how workers report throughput):

Workers run in separate processes. They communicate via `multiprocessing.Value` objects:
- `shared_bytes` — total bytes sent to DB across all workers
- `shared_rows` — total rows inserted
- `shared_errors` — error count
- `stop_flag` — set to 1 to tell all workers to stop
- Protected by a `Lock` to prevent race conditions

#### Failure Detection:

The script checks for:
- **Error count > 10** — stops the round, marks as broken
- **Connection refused** — worker can't connect, records error
- **Connection lost mid-round** — worker pings DB, exits if dead
- **Throughput drop** — warns if throughput drops >5% from previous round (saturation signal)

---

## What We Ran (Execution Timeline)

```
T+0 min    terraform apply → Create 27 resources (VPC, 6 Aurora instances, EC2)
T+10 min   All Aurora instances available, EC2 bootstrapped
T+11 min   SCP benchmark scripts to EC2
T+12 min   python3.11 generate_data.py → Generate 20×1 GB files (20 GB total)
T+16 min   Start PostgreSQL stress test (16 rounds × ~70s each)
           Round 1:  4 workers  → 154 MB/s
           Round 2:  8 workers  → 159 MB/s
           ...
           Round 11: 44 workers → 192 MB/s ★ peak
           ...
           Round 16: 64 workers → 110 MB/s (graceful degradation)
T+35 min   PostgreSQL test complete — 0 errors, never broke
T+35 min   Start MySQL stress test (16 rounds × ~70s each)
           Round 1:  4 workers  → 66 MB/s
           Round 2:  8 workers  → 51 MB/s (dropping)
           Round 3:  12 workers → 34 MB/s (dropping fast)
           Round 4:  16 workers → 21 MB/s (barely alive)
           Round 5:  20 workers → 0 MB/s  (STALLED)
           Round 6-16: 0 MB/s   (never recovered)
T+54 min   MySQL test complete — stalled from Round 5 onward
T+55 min   Download results to local machine
T+55 min   terraform destroy → Destroy all 27 resources
T+67 min   All infrastructure gone, billing stopped
```

**Total wall clock: ~67 minutes**
**Total cost: ~$10**

---

## How Throughput Was Measured

Both engines measured identically:

1. Worker streams a pre-generated 1 GB TSV file into the database
2. After the DB command completes (`COPY` or `LOAD DATA`), worker updates shared counter with `os.path.getsize(file)` — the actual file size in bytes
3. Orchestrator reads the shared counter every 5 seconds and calculates:
   - **Instant throughput:** `(current_bytes - previous_bytes) / 5 seconds`
   - **Average throughput:** `total_bytes / elapsed_seconds`
4. Same files used for both engines = same byte count = fair comparison

---

## Why This Worked (vs. the First Experiment)

| | Experiment v1 | Experiment v2 (this one) |
|---|---|---|
| Data generation | On-the-fly in Python | Pre-generated to disk |
| Bottleneck | EC2 CPU (data gen) | Database (as intended) |
| Ingestion method (PG) | `executemany` batch INSERT | `COPY FROM STDIN` (1 GB file) |
| Ingestion method (MySQL) | `executemany` batch INSERT | `LOAD DATA LOCAL INFILE` (1 GB file) |
| Throughput measurement | Different methods per engine | Same method (file bytes) |
| Load pattern | Fixed targets (200/400/1000) | Ramp up until failure |
| Workers | 16 (fixed) | 4 → 8 → 12 → ... → 64 (ramping) |
| Max throughput reached | ~150 MB/s (EC2 limit) | 192 MB/s PG / 66 MB/s MySQL (DB limits) |
| Found breaking point? | No | Yes (MySQL stalled at 20 workers) |
| Useful result? | No | Yes |
