#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TF_DIR="$PROJECT_DIR/terraform"

echo "Destroying all stress test infrastructure..."
cd "$TF_DIR"
terraform destroy -auto-approve
echo "All infrastructure destroyed. Billing stopped."
