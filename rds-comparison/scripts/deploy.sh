#!/bin/bash
###############################################################################
# Deploy Aurora Benchmark Infrastructure
# 1 Writer + 2 Readers per engine | Enhanced Monitoring | Performance Insights
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TF_DIR="$PROJECT_DIR/terraform"
BENCH_DIR="$PROJECT_DIR/benchmark"

echo "╔═══════════════════════════════════════════════════════════════════╗"
echo "║    Aurora Benchmark — Deploy (1W + 2R per engine)               ║"
echo "╚═══════════════════════════════════════════════════════════════════╝"
echo ""

command -v terraform >/dev/null 2>&1 || { echo "ERROR: terraform not found"; exit 1; }
command -v aws >/dev/null 2>&1 || { echo "ERROR: aws CLI not found"; exit 1; }
aws sts get-caller-identity >/dev/null 2>&1 || { echo "ERROR: AWS creds not configured"; exit 1; }
echo "AWS: $(aws sts get-caller-identity --query 'Arn' --output text)"

SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_rsa}"
if [ ! -f "${SSH_KEY_PATH}.pub" ]; then
    read -rp "Path to SSH public key: " SSH_KEY_PATH
    SSH_KEY_PATH="${SSH_KEY_PATH%.pub}"
fi
echo "SSH key: ${SSH_KEY_PATH}.pub"

if [ -z "${TF_VAR_db_password:-}" ]; then
    DB_PASS="BenchM@rk$(date +%s | shasum | head -c 8)!"
    export TF_VAR_db_password="$DB_PASS"
    echo "DB password: $DB_PASS"
fi

MY_IP=$(curl -s https://checkip.amazonaws.com 2>/dev/null || echo "0.0.0.0")
export TF_VAR_ssh_cidr="${MY_IP}/32"
export TF_VAR_ssh_public_key="$(cat "${SSH_KEY_PATH}.pub")"
echo "SSH from: ${TF_VAR_ssh_cidr}"
echo ""

cd "$TF_DIR"
terraform init -input=false
echo ""
terraform plan -input=false -out=benchmark.tfplan

echo ""
read -rp "Deploy? (yes/no): " CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "Aborted."; exit 0; }

echo ""
echo "Deploying (Aurora takes ~8-12 min to spin up)..."
terraform apply -input=false benchmark.tfplan

EC2_IP=$(terraform output -raw ec2_public_ip)

echo ""
echo "Waiting 90s for EC2 bootstrap..."
sleep 90

echo "Uploading benchmark scripts..."
scp -o StrictHostKeyChecking=no -o ConnectTimeout=30 -i "$SSH_KEY_PATH" \
    "$BENCH_DIR"/*.py "$BENCH_DIR"/*.sh "$BENCH_DIR"/requirements.txt "$BENCH_DIR"/*.md \
    "ec2-user@${EC2_IP}:/home/ec2-user/benchmark/"

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  READY!"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo "  ssh -i $SSH_KEY_PATH ec2-user@$EC2_IP"
echo "  bash /home/ec2-user/check_ready.sh"
echo "  cd /home/ec2-user/benchmark && bash run_benchmark.sh"
echo ""
echo "  When done: bash $SCRIPT_DIR/destroy.sh"
echo "  Cost: ~\$10/hour (6 Aurora instances + 1 EC2)"
echo ""

cat > "$PROJECT_DIR/.benchmark_info" << EOF
EC2_IP=$EC2_IP
SSH_KEY=$SSH_KEY_PATH
DB_PASSWORD=$TF_VAR_db_password
DEPLOYED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
