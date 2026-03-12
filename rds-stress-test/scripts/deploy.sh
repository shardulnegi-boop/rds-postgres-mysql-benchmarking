#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TF_DIR="$PROJECT_DIR/terraform"
BENCH_DIR="$PROJECT_DIR/benchmark"

echo "╔═══════════════════════════════════════════════════════════════════╗"
echo "║    Aurora Stress Test v2 — Deploy                                ║"
echo "╚═══════════════════════════════════════════════════════════════════╝"

command -v terraform >/dev/null 2>&1 || { echo "ERROR: terraform not found"; exit 1; }
aws sts get-caller-identity >/dev/null 2>&1 || { echo "ERROR: AWS creds not configured"; exit 1; }

SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_rsa}"
if [ ! -f "${SSH_KEY_PATH}.pub" ]; then
    read -rp "Path to SSH public key: " SSH_KEY_PATH
    SSH_KEY_PATH="${SSH_KEY_PATH%.pub}"
fi

if [ -z "${TF_VAR_db_password:-}" ]; then
    export TF_VAR_db_password="StressTest$(date +%s | shasum | head -c 8)x"
fi

export TF_VAR_ssh_public_key="$(cat "${SSH_KEY_PATH}.pub")"

cd "$TF_DIR"
terraform init -input=false
terraform apply -input=false -auto-approve

EC2_IP=$(terraform output -raw ec2_public_ip)

echo ""
echo "Waiting 90s for EC2 bootstrap..."
sleep 90

echo "Uploading benchmark scripts..."
scp -o StrictHostKeyChecking=no -o ConnectTimeout=30 -i "$SSH_KEY_PATH" \
    "$BENCH_DIR"/*.py "$BENCH_DIR"/*.sh "$BENCH_DIR"/requirements.txt \
    "ec2-user@${EC2_IP}:/home/ec2-user/benchmark/"

echo ""
echo "READY! SSH: ssh -i $SSH_KEY_PATH ec2-user@$EC2_IP"
echo "Run:  cd benchmark && bash run_stress_test.sh"
