#!/usr/bin/env python3
"""
Pre-generate CSV data files for stress testing.
Removes Python CPU as the bottleneck during the actual test.

Generates tab-delimited files compatible with both:
  - PostgreSQL COPY FROM STDIN
  - MySQL LOAD DATA LOCAL INFILE
"""

import argparse
import json
import os
import random
import string
import sys
import time

ROW_SIZE_TARGET = 1750  # ~1.75KB per row
PAYLOAD_SIZE = 1024

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


def generate_file(file_path, target_bytes):
    """Generate a single tab-delimited data file."""
    written = 0
    rows = 0
    with open(file_path, "w", buffering=1024 * 1024) as f:
        while written < target_bytes:
            device_id = random.choice(DEVICE_IDS)
            metric = random.choice(METRIC_NAMES)
            value = random.uniform(0, 10000)
            tags = random.choice(TAG_TEMPLATES)
            payload = "".join(random.choices(PAYLOAD_CHARS, k=PAYLOAD_SIZE))
            line = f"{device_id}\t{metric}\t{value}\t{tags}\t{payload}\n"
            f.write(line)
            written += len(line)
            rows += 1
    return written, rows


def main():
    parser = argparse.ArgumentParser(description="Pre-generate data files for stress test")
    parser.add_argument("--output-dir", default="/data", help="Directory to write files")
    parser.add_argument("--num-files", type=int, default=20, help="Number of files to generate")
    parser.add_argument("--file-size-mb", type=int, default=1024, help="Target size per file in MB")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    target_bytes = args.file_size_mb * 1024 * 1024

    print(f"Generating {args.num_files} x {args.file_size_mb}MB files in {args.output_dir}/")
    total_bytes = 0
    total_rows = 0
    t0 = time.time()

    for i in range(args.num_files):
        path = os.path.join(args.output_dir, f"data_{i:03d}.tsv")
        ft0 = time.time()
        fbytes, frows = generate_file(path, target_bytes)
        elapsed = time.time() - ft0
        total_bytes += fbytes
        total_rows += frows
        print(f"  [{i+1}/{args.num_files}] {path}: {fbytes / (1024**2):.0f} MB, "
              f"{frows:,} rows, {elapsed:.1f}s ({fbytes / (1024**2) / elapsed:.0f} MB/s)")

    elapsed = time.time() - t0
    print(f"\nDone: {total_bytes / (1024**3):.1f} GB, {total_rows:,} rows in {elapsed:.0f}s")
    print(f"Avg generation speed: {total_bytes / (1024**2) / elapsed:.0f} MB/s")


if __name__ == "__main__":
    main()
