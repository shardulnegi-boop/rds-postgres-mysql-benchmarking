# Aurora MySQL vs Aurora PostgreSQL — Instance Comparison
## Target: 200 MB/s Sustained Ingestion Rate

---

## TL;DR Recommendation

| Criteria | Winner |
|----------|--------|
| **Raw write throughput** | Aurora MySQL (slight edge) |
| **Complex data types / JSON** | Aurora PostgreSQL |
| **Extensions ecosystem** | Aurora PostgreSQL |
| **Cost efficiency** | Aurora PostgreSQL (Graviton) |
| **Overall for 200 MB/s ingest** | **Aurora PostgreSQL** |

**Recommended Instance:** `db.r7g.4xlarge` (minimum) or `db.r7g.8xlarge` (safe) with **I/O-Optimized** storage.

---

## 1. Ingestion Math — Why Instance Size Matters

```
Sustained ingestion:  200 MB/s
                    = 1.6 Gbps network throughput (minimum)
                    = ~17.28 TB/day raw data

Aurora write amplification: Each write is replicated to 6 storage nodes
Effective storage I/O:      ~1,200 MB/s across the storage layer

Estimated IOPS (8KB pages):  ~25,000 write IOPS (PostgreSQL)
Estimated IOPS (16KB pages): ~12,500 write IOPS (MySQL)
```

You need an instance with:
- **Network bandwidth** >= 3-4 Gbps (2x headroom over 1.6 Gbps)
- **Sufficient vCPUs** for WAL/binlog processing at this rate
- **Large buffer pool/shared_buffers** to reduce random I/O

---

## 2. Instance Comparison (Graviton-based, recommended)

| Instance | vCPUs | Memory | Network | MySQL Fit | PostgreSQL Fit | On-Demand $/hr (approx, us-east-1) |
|----------|-------|--------|---------|-----------|----------------|-------------------------------------|
| `db.r6g.2xlarge` | 8 | 64 GB | Up to 10 Gbps | Tight | Tight | ~$0.86 |
| `db.r6g.4xlarge` | 16 | 128 GB | Up to 10 Gbps | Possible | Possible | ~$1.72 |
| **`db.r7g.4xlarge`** | 16 | 128 GB | Up to 15 Gbps | **Good** | **Good** | ~$1.98 |
| `db.r6g.8xlarge` | 32 | 256 GB | 12 Gbps | Good | Good | ~$3.44 |
| **`db.r7g.8xlarge`** | 32 | 256 GB | 15 Gbps | **Best** | **Best** | ~$3.97 |
| `db.r8g.4xlarge` | 16 | 128 GB | Up to 15 Gbps | Great | Great | ~$2.10 (est) |

> **Note:** Pricing is approximate. Always check [AWS Aurora Pricing](https://aws.amazon.com/rds/aurora/pricing/) for current rates. Reserved Instances or Savings Plans can reduce costs by 30-60%.

---

## 3. Aurora MySQL vs Aurora PostgreSQL — Deep Comparison

### 3a. Write Performance

| Factor | Aurora MySQL | Aurora PostgreSQL |
|--------|-------------|-------------------|
| AWS marketing claim | 5x standard MySQL | 3x standard PostgreSQL |
| Bulk INSERT speed | Faster for simple row inserts | Faster with COPY command for bulk loads |
| Write concurrency | Good with InnoDB | Better with MVCC under high concurrency |
| Replication lag | ~10-20ms to replicas | ~10-20ms to replicas |
| Max write throughput | Single writer endpoint | Single writer (Multi-Master available but limited) |
| Parallel write support | Limited | Better parallel query support |

### 3b. For Your ML/Inference Workload

| Factor | Aurora MySQL | Aurora PostgreSQL |
|--------|-------------|-------------------|
| JSON/JSONB support | Basic JSON | **Native JSONB** (indexed, queryable) |
| Array types | No native arrays | **Native array types** |
| Extensions | Limited | **Rich ecosystem** (PostGIS, pg_vector, etc.) |
| Vector search (embeddings) | Not native | **pgvector extension** |
| Partitioning | Range/List/Hash | **Declarative partitioning** (more flexible) |
| Full-text search | Basic FULLTEXT | **Built-in tsvector/tsquery** |
| Analytical queries | Moderate | **Better for complex analytics** |

### 3c. Operational Comparison

| Factor | Aurora MySQL | Aurora PostgreSQL |
|--------|-------------|-------------------|
| Max DB size | 128 TB | 128 TB |
| Read replicas | Up to 15 | Up to 15 |
| Failover time | ~30s | ~30s |
| Point-in-time recovery | Yes (5 min granularity) | Yes (5 min granularity) |
| Global Database | Yes | Yes |
| Serverless v2 | Yes | Yes |
| Blue/Green deployments | Yes | Yes |
| Parallel query | Yes (MySQL 5.7 compat) | Yes |

---

## 4. Storage: I/O-Optimized vs Standard

For 200 MB/s ingestion, **I/O-Optimized is strongly recommended**.

| Storage Type | Storage Cost | I/O Cost | Best For |
|-------------|-------------|----------|----------|
| **Standard** | $0.10/GB-month | $0.20 per 1M requests | Light I/O (< 25% of total cost) |
| **I/O-Optimized** | $0.225/GB-month | **$0 (included)** | Heavy I/O (>= 25% of total cost) |

### Cost Estimate at 200 MB/s

```
Standard Storage:
  Storage: 10 TB * $0.10 = $1,000/month
  Write IOPS: ~25,000 IOPS * 86,400 sec/day * 30 days = ~64.8B I/Os/month
  I/O Cost: 64,800 * $0.20 = ~$12,960/month
  Total Storage Cost: ~$13,960/month

I/O-Optimized:
  Storage: 10 TB * $0.225 = $2,250/month
  I/O Cost: $0
  Total Storage Cost: ~$2,250/month

SAVINGS WITH I/O-OPTIMIZED: ~$11,710/month (~84% savings)
```

> At 200 MB/s sustained write, I/O-Optimized is a no-brainer.

---

## 5. Final Recommendation

### Engine: **Aurora PostgreSQL**

Reasons:
1. **JSONB support** — critical for ML metadata, inference results, model configs
2. **pgvector** — if you ever need vector similarity search for embeddings
3. **COPY command** — superior bulk ingestion performance vs INSERT
4. **Declarative partitioning** — essential for managing 17+ TB/day of data
5. **Better concurrent write handling** under MVCC
6. **Rich extension ecosystem** for future flexibility

### Instance: **`db.r7g.4xlarge`** (start) → scale to **`db.r7g.8xlarge`** if needed

| Config | Value |
|--------|-------|
| Engine | Aurora PostgreSQL 16.x |
| Instance | `db.r7g.4xlarge` (16 vCPU, 128 GB) |
| Storage | I/O-Optimized |
| Read Replicas | 2 (for read offloading) |
| Multi-AZ | Yes (built into Aurora) |
| Estimated Monthly Cost | ~$1,425 (instance) + ~$2,250 (storage @ 10TB) = **~$3,675/month** |

### If You Pick MySQL:

| Config | Value |
|--------|-------|
| Engine | Aurora MySQL 8.0 |
| Instance | `db.r7g.4xlarge` (16 vCPU, 128 GB) |
| Storage | I/O-Optimized |
| Read Replicas | 2 |
| Estimated Monthly Cost | ~$1,425 (instance) + ~$2,250 (storage @ 10TB) = **~$3,675/month** |

> Instance pricing is nearly identical between MySQL and PostgreSQL. The difference is in capabilities and workload fit.

---

## 6. Key Tuning Parameters

### Aurora PostgreSQL (for 200 MB/s writes)
```
shared_buffers = 32GB              # ~25% of 128GB RAM
effective_cache_size = 96GB        # ~75% of RAM
wal_buffers = 256MB                # Large WAL buffer for write throughput
max_wal_size = 8GB                 # Allow large WAL before checkpoint
checkpoint_completion_target = 0.9
work_mem = 256MB                   # For sorting/hashing
maintenance_work_mem = 2GB         # For VACUUM, CREATE INDEX
max_connections = 500              # Use connection pooler (PgBouncer)
```

### Aurora MySQL (for 200 MB/s writes)
```
innodb_buffer_pool_size = 96GB     # ~75% of 128GB RAM
innodb_log_buffer_size = 256MB     # Large log buffer
innodb_flush_log_at_trx_commit = 2 # Async flush (1 for durability)
innodb_write_io_threads = 16       # Match vCPU count
max_connections = 500              # Use connection pooler (ProxySQL)
```

---

## Sources

- [AWS Aurora Features](https://aws.amazon.com/rds/aurora/features/)
- [AWS Aurora Instance Types](https://aws.amazon.com/rds/aurora/instance-types/)
- [AWS Aurora Pricing](https://aws.amazon.com/rds/aurora/pricing/)
- [Aurora I/O-Optimized vs Standard (CloudFix)](https://cloudfix.com/blog/aurora-io-optimized-vs-standard/)
- [Aurora vs RDS Guide (Bytebase)](https://www.bytebase.com/blog/aurora-vs-rds/)
- [Aurora Instance Types & Pricing (Sedai)](https://sedai.io/blog/aurora-instance-types)
- [Aurora PostgreSQL Graviton4 R8gd Benchmarks (AWS Blog)](https://aws.amazon.com/blogs/database/improve-aurora-postgresql-throughput-by-up-to-165-and-price-performance-ratio-by-up-to-120-using-optimized-reads-on-aws-graviton4-based-r8gd-instances/)
