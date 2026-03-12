#!/bin/bash
###############################################################################
# Destroy ALL Aurora Benchmark Infrastructure
#
# Run this immediately after benchmarking to stop billing.
# Estimated savings: ~$6/hour
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TF_DIR="$PROJECT_DIR/terraform"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     DESTROY Aurora Benchmark Infrastructure                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Show what exists
if [ -f "$PROJECT_DIR/.benchmark_info" ]; then
    echo "Current deployment:"
    cat "$PROJECT_DIR/.benchmark_info"
    echo ""
fi

echo "WARNING: This will permanently destroy:"
echo "  - Aurora PostgreSQL cluster (aurora-bench-pg)"
echo "  - Aurora MySQL cluster (aurora-bench-mysql)"
echo "  - EC2 load generator instance"
echo "  - VPC and all networking resources"
echo "  - ALL data in both databases"
echo ""

read -rp "Type 'destroy' to confirm: " CONFIRM
if [ "$CONFIRM" != "destroy" ]; then
    echo "Aborted."
    exit 0
fi

echo ""

# ── Optional: Download results before destroying ────────
if [ -f "$PROJECT_DIR/.benchmark_info" ]; then
    source "$PROJECT_DIR/.benchmark_info"
    echo "Attempting to download benchmark results before destroying..."
    scp -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        -i "$SSH_KEY" \
        "ec2-user@${EC2_IP}:/home/ec2-user/results/*" \
        "$PROJECT_DIR/results/" 2>/dev/null && {
        echo "Results downloaded to $PROJECT_DIR/results/"
    } || {
        echo "Could not download results (EC2 may already be gone)"
    }
    echo ""
fi

# ── Terraform Destroy ───────────────────────────────────
cd "$TF_DIR"

echo "Destroying all infrastructure..."
terraform destroy -auto-approve

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ALL INFRASTRUCTURE DESTROYED"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Billing has stopped for:"
echo "    - 2x Aurora db.r7g.4xlarge instances"
echo "    - 1x EC2 c7g.4xlarge instance"
echo "    - Aurora I/O-Optimized storage"
echo ""

# Cleanup local files
rm -f "$PROJECT_DIR/.benchmark_info"
rm -f "$TF_DIR/benchmark.tfplan"
rm -f "$TF_DIR/terraform.tfstate.backup"

echo "Local cleanup complete."
