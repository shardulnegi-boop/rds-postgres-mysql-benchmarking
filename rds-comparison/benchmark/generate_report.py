#!/usr/bin/env python3
"""
Generates an HTML performance report with embedded charts.
Pulls CloudWatch metrics + reads DB monitor JSONL files + load test results.
"""

import argparse
import base64
import glob
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ──────────────────────────────────────────────────────────
# CloudWatch Metrics Pull
# ──────────────────────────────────────────────────────────

CW_METRICS = [
    "CPUUtilization",
    "FreeableMemory",
    "DatabaseConnections",
    "WriteIOPS",
    "ReadIOPS",
    "WriteThroughput",
    "ReadThroughput",
    "WriteLatency",
    "ReadLatency",
    "NetworkReceiveThroughput",
    "NetworkTransmitThroughput",
    "BufferCacheHitRatio",
    "DiskQueueDepth",
    "SwapUsage",
    "AuroraReplicaLag",
]

METRIC_UNITS = {
    "CPUUtilization": "%",
    "FreeableMemory": "GB",
    "DatabaseConnections": "count",
    "WriteIOPS": "ops/s",
    "ReadIOPS": "ops/s",
    "WriteThroughput": "MB/s",
    "ReadThroughput": "MB/s",
    "WriteLatency": "ms",
    "ReadLatency": "ms",
    "NetworkReceiveThroughput": "MB/s",
    "NetworkTransmitThroughput": "MB/s",
    "BufferCacheHitRatio": "%",
    "DiskQueueDepth": "depth",
    "SwapUsage": "MB",
    "AuroraReplicaLag": "ms",
}


def pull_cloudwatch(region, instance_id, start_time, end_time):
    cw = boto3.client("cloudwatch", region_name=region)
    data = {}

    for metric_name in CW_METRICS:
        try:
            resp = cw.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName=metric_name,
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=60,
                Statistics=["Average", "Maximum"],
            )
            points = sorted(resp["Datapoints"], key=lambda x: x["Timestamp"])
            data[metric_name] = points
        except Exception as e:
            data[metric_name] = []

    return data


def normalize_metric(metric_name, value):
    if metric_name == "FreeableMemory":
        return value / (1024 ** 3)  # bytes → GB
    if metric_name in ("WriteThroughput", "ReadThroughput",
                       "NetworkReceiveThroughput", "NetworkTransmitThroughput"):
        return value / (1024 * 1024)  # bytes → MB/s
    if metric_name in ("WriteLatency", "ReadLatency"):
        return value * 1000  # seconds → ms
    if metric_name == "AuroraReplicaLag":
        return value * 1000  # seconds → ms
    if metric_name == "SwapUsage":
        return value / (1024 * 1024)  # bytes → MB
    return value


# ──────────────────────────────────────────────────────────
# Chart Generation
# ──────────────────────────────────────────────────────────

COLORS = {"postgresql": "#336791", "mysql": "#F29111"}


def fig_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="#1a1a2e")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return b64


def make_throughput_bar_chart(results):
    """Bar chart: target vs actual for both engines at each rate."""
    fig, ax = plt.subplots(figsize=(10, 5), facecolor="#1a1a2e")
    ax.set_facecolor("#16213e")

    rates = sorted(set(r["target_mbps"] for r in results))
    engines = ["postgresql", "mysql"]
    x = range(len(rates))
    width = 0.35

    for i, eng in enumerate(engines):
        actuals = []
        for rate in rates:
            match = [r for r in results if r["engine"] == eng and r["target_mbps"] == rate]
            actuals.append(match[0]["actual_mbps"] if match else 0)
        offset = -width / 2 + i * width
        bars = ax.bar([xi + offset for xi in x], actuals, width, label=eng.title(),
                      color=COLORS[eng], alpha=0.9)
        for bar, val in zip(bars, actuals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                    f"{val:.0f}", ha="center", va="bottom", color="white", fontsize=9)

    # Target lines
    for j, rate in enumerate(rates):
        ax.hlines(rate, j - 0.4, j + 0.4, colors="red", linestyles="dashed", alpha=0.7)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{r} MB/s\ntarget" for r in rates], color="white")
    ax.set_ylabel("Actual MB/s", color="white")
    ax.set_title("Throughput: Target vs Actual", color="white", fontsize=14)
    ax.legend(facecolor="#16213e", edgecolor="white", labelcolor="white")
    ax.tick_params(colors="white")
    ax.spines[:].set_color("#333")
    return fig_to_base64(fig)


def make_cw_chart(title, metric_name, pg_data, mysql_data):
    """Line chart comparing a CloudWatch metric for PG vs MySQL writer."""
    fig, ax = plt.subplots(figsize=(10, 4), facecolor="#1a1a2e")
    ax.set_facecolor("#16213e")

    for label, points, color in [
        ("PostgreSQL", pg_data, COLORS["postgresql"]),
        ("MySQL", mysql_data, COLORS["mysql"]),
    ]:
        if points:
            times = [p["Timestamp"] for p in points]
            vals = [normalize_metric(metric_name, p["Average"]) for p in points]
            ax.plot(times, vals, color=color, label=label, linewidth=2, alpha=0.9)

    unit = METRIC_UNITS.get(metric_name, "")
    ax.set_ylabel(unit, color="white")
    ax.set_title(title, color="white", fontsize=13)
    ax.legend(facecolor="#16213e", edgecolor="white", labelcolor="white")
    ax.tick_params(colors="white")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.spines[:].set_color("#333")
    fig.autofmt_xdate()
    return fig_to_base64(fig)


def make_internals_chart(title, pg_samples, mysql_samples, key_path, ylabel=""):
    """Line chart from DB monitor JSONL samples."""
    fig, ax = plt.subplots(figsize=(10, 4), facecolor="#1a1a2e")
    ax.set_facecolor("#16213e")

    for label, samples, color in [
        ("PostgreSQL", pg_samples, COLORS["postgresql"]),
        ("MySQL", mysql_samples, COLORS["mysql"]),
    ]:
        times = []
        vals = []
        for s in samples:
            try:
                t = datetime.fromisoformat(s["ts"])
                v = s
                for k in key_path.split("."):
                    v = v.get(k, {}) if isinstance(v, dict) else {}
                if isinstance(v, (int, float)):
                    times.append(t)
                    vals.append(float(v))
            except (KeyError, TypeError, ValueError):
                continue
        if times:
            ax.plot(times, vals, color=color, label=label, linewidth=2, alpha=0.9)

    ax.set_ylabel(ylabel, color="white")
    ax.set_title(title, color="white", fontsize=13)
    ax.legend(facecolor="#16213e", edgecolor="white", labelcolor="white")
    ax.tick_params(colors="white")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.spines[:].set_color("#333")
    fig.autofmt_xdate()
    return fig_to_base64(fig)


# ──────────────────────────────────────────────────────────
# HTML Report
# ──────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Aurora Benchmark Report</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0f0f23; color: #e0e0e0; font-family: 'Segoe UI', system-ui, sans-serif; padding: 20px; }}
  h1 {{ color: #00d4ff; text-align: center; margin: 20px 0; font-size: 28px; }}
  h2 {{ color: #ff6b35; margin: 30px 0 15px; border-bottom: 1px solid #333; padding-bottom: 8px; }}
  h3 {{ color: #aaa; margin: 20px 0 10px; }}
  .meta {{ text-align: center; color: #888; margin-bottom: 30px; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
  .card {{ background: #1a1a2e; border-radius: 10px; padding: 20px; text-align: center; border: 1px solid #333; }}
  .card .value {{ font-size: 32px; font-weight: bold; color: #00d4ff; }}
  .card .label {{ color: #888; margin-top: 5px; }}
  .card.pass {{ border-color: #4caf50; }} .card.pass .value {{ color: #4caf50; }}
  .card.fail {{ border-color: #f44336; }} .card.fail .value {{ color: #f44336; }}
  .card.degraded {{ border-color: #ff9800; }} .card.degraded .value {{ color: #ff9800; }}
  table {{ width: 100%; border-collapse: collapse; margin: 15px 0; background: #1a1a2e; border-radius: 8px; overflow: hidden; }}
  th {{ background: #16213e; color: #00d4ff; padding: 12px; text-align: left; }}
  td {{ padding: 10px 12px; border-top: 1px solid #222; }}
  tr:hover {{ background: #16213e; }}
  .pass-badge {{ color: #4caf50; font-weight: bold; }}
  .fail-badge {{ color: #f44336; font-weight: bold; }}
  .degraded-badge {{ color: #ff9800; font-weight: bold; }}
  .chart {{ background: #1a1a2e; border-radius: 10px; padding: 10px; margin: 15px 0; border: 1px solid #222; }}
  .chart img {{ width: 100%; border-radius: 5px; }}
  .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
  @media (max-width: 900px) {{ .chart-grid {{ grid-template-columns: 1fr; }} }}
  .guidance {{ background: #1a1a2e; border-radius: 10px; padding: 20px; margin: 15px 0; border-left: 4px solid #00d4ff; }}
  .guidance li {{ margin: 8px 0; padding-left: 5px; }}
</style>
</head>
<body>
<h1>Aurora MySQL vs PostgreSQL — Benchmark Report</h1>
<p class="meta">Instance: db.r7g.4xlarge (16 vCPU, 128GB) | Storage: I/O-Optimized | 1 Writer + 2 Readers<br>
Generated: {generated_at}</p>

<h2>Executive Summary</h2>
<div class="summary-grid">{summary_cards}</div>

<h2>Results Table</h2>
<table>
<tr><th>Engine</th><th>Target MB/s</th><th>Actual MB/s</th><th>Achieved</th><th>Total GB</th><th>Rows</th><th>Errors</th><th>Status</th></tr>
{results_rows}
</table>

<h2>Throughput Comparison</h2>
<div class="chart"><img src="data:image/png;base64,{throughput_chart}" /></div>

<h2>CloudWatch Metrics — Writer Instances</h2>
<div class="chart-grid">{cw_charts}</div>

<h2>Database Internals</h2>
<div class="chart-grid">{internal_charts}</div>

<h2>Replication Lag</h2>
<div class="chart"><img src="data:image/png;base64,{replication_chart}" /></div>

<h2>Interpretation Guide</h2>
<div class="guidance">
<ul>
<li><b>CPU > 80%</b> — Engine is compute-bound. Consider larger instance or connection pooling.</li>
<li><b>FreeableMemory < 10%</b> — Buffer pool pressure. Rows spilling to disk, expect latency spike.</li>
<li><b>WriteLatency > 5ms</b> — Storage layer struggling. Aurora storage is distributed; high latency means quorum writes are slow.</li>
<li><b>DiskQueueDepth > 10</b> — I/O requests queuing up. Storage can't keep pace with write rate.</li>
<li><b>BufferCacheHitRatio < 99%</b> — Reads hitting disk instead of cache. Increase buffer pool or reduce working set.</li>
<li><b>ReplicaLag > 100ms</b> — Readers falling behind writer. At sustained lag, read-after-write consistency breaks.</li>
<li><b>Deadlocks / Lock Waits</b> — Concurrent writes conflicting. Review schema, indexing, and batch sizes.</li>
<li><b>PASS</b> = achieved >= 80% of target | <b>DEGRADED</b> = 50-80% | <b>FAIL</b> = below 50%</li>
</ul>
</div>

</body>
</html>"""


def load_results(results_dir):
    results = []
    for f in sorted(glob.glob(os.path.join(results_dir, "*.log"))):
        with open(f) as fh:
            for line in fh:
                if line.startswith("JSON_RESULT:"):
                    results.append(json.loads(line.replace("JSON_RESULT:", "", 1)))
    return results


def load_monitor_samples(results_dir, engine):
    samples = []
    for f in sorted(glob.glob(os.path.join(results_dir, f"monitor_{engine}_*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return samples


def main():
    parser = argparse.ArgumentParser(description="Generate HTML benchmark report")
    parser.add_argument("--results-dir", default="/home/ec2-user/results")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", default="/home/ec2-user/results/benchmark_report.html")
    parser.add_argument("--start-time", help="ISO format benchmark start time")
    parser.add_argument("--end-time", help="ISO format benchmark end time")
    args = parser.parse_args()

    print("Loading benchmark results...")
    results = load_results(args.results_dir)
    if not results:
        print("ERROR: No results found!")
        sys.exit(1)

    # Time range for CloudWatch
    if args.start_time and args.end_time:
        start_t = datetime.fromisoformat(args.start_time).replace(tzinfo=timezone.utc)
        end_t = datetime.fromisoformat(args.end_time).replace(tzinfo=timezone.utc)
    else:
        end_t = datetime.now(timezone.utc)
        start_t = end_t - timedelta(minutes=45)

    # Pull CloudWatch for writers
    print("Pulling CloudWatch metrics (this takes ~30s)...")
    pg_cw = pull_cloudwatch(args.region, "aurora-bench-pg-writer", start_t, end_t)
    mysql_cw = pull_cloudwatch(args.region, "aurora-bench-mysql-writer", start_t, end_t)

    # Load DB monitor samples
    pg_samples = load_monitor_samples(args.results_dir, "postgresql")
    mysql_samples = load_monitor_samples(args.results_dir, "mysql")

    # ── Build charts ──
    print("Generating charts...")
    throughput_chart = make_throughput_bar_chart(results)

    cw_chart_configs = [
        ("CPU Utilization", "CPUUtilization"),
        ("Freeable Memory", "FreeableMemory"),
        ("Write IOPS", "WriteIOPS"),
        ("Write Throughput", "WriteThroughput"),
        ("Write Latency", "WriteLatency"),
        ("Read Latency", "ReadLatency"),
        ("Network Receive", "NetworkReceiveThroughput"),
        ("Network Transmit", "NetworkTransmitThroughput"),
        ("Buffer Cache Hit Ratio", "BufferCacheHitRatio"),
        ("Disk Queue Depth", "DiskQueueDepth"),
        ("Connections", "DatabaseConnections"),
        ("Swap Usage", "SwapUsage"),
    ]
    cw_charts_html = ""
    for title, metric in cw_chart_configs:
        b64 = make_cw_chart(title, metric, pg_cw.get(metric, []), mysql_cw.get(metric, []))
        cw_charts_html += f'<div class="chart"><img src="data:image/png;base64,{b64}" /></div>\n'

    # Internal metrics charts
    internal_charts_html = ""
    internal_configs = [
        ("Cache Hit Ratio", "cache_hit_ratio", "%"),
        ("Replication Lag", "replication_lag_sec", "seconds"),
    ]
    for title, key, unit in internal_configs:
        b64 = make_internals_chart(title, pg_samples, mysql_samples, key, unit)
        internal_charts_html += f'<div class="chart"><img src="data:image/png;base64,{b64}" /></div>\n'

    # Replication chart
    replication_chart = make_internals_chart(
        "Replication Lag Over Time", pg_samples, mysql_samples, "replication_lag_sec", "seconds"
    )

    # ── Summary cards ──
    cards_html = ""
    for r in sorted(results, key=lambda x: (x["target_mbps"], x["engine"])):
        status_class = r["status"].lower()
        cards_html += (
            f'<div class="card {status_class}">'
            f'<div class="value">{r["actual_mbps"]:.0f} MB/s</div>'
            f'<div class="label">{r["engine"].title()} @ {r["target_mbps"]} target</div>'
            f'</div>\n'
        )

    # ── Results table rows ──
    rows_html = ""
    for r in sorted(results, key=lambda x: (x["target_mbps"], x["engine"])):
        badge = f'{r["status"].lower()}-badge'
        rows_html += (
            f'<tr><td>{r["engine"].title()}</td><td>{r["target_mbps"]}</td>'
            f'<td>{r["actual_mbps"]:.1f}</td><td>{r.get("achieved_pct", 0):.0f}%</td>'
            f'<td>{r["total_gb"]:.2f}</td><td>{r["total_rows"]:,}</td>'
            f'<td>{r["errors"]:,}</td><td class="{badge}">{r["status"]}</td></tr>\n'
        )

    # ── Write HTML ──
    html = HTML_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        summary_cards=cards_html,
        results_rows=rows_html,
        throughput_chart=throughput_chart,
        cw_charts=cw_charts_html,
        internal_charts=internal_charts_html,
        replication_chart=replication_chart,
    )

    with open(args.output, "w") as f:
        f.write(html)

    print(f"\nReport saved to: {args.output}")
    file_size = os.path.getsize(args.output) / (1024 * 1024)
    print(f"Size: {file_size:.1f} MB")
    print("Download it: scp ec2-user@<EC2_IP>:/home/ec2-user/results/benchmark_report.html .")


if __name__ == "__main__":
    main()
