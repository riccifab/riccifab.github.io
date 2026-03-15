#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
ENV_FILE="$ROOT_DIR/.env.local"
LOCAL_CREDENTIALS_DIR="$ROOT_DIR/local/credentials"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing local Python environment. Run:"
  echo "  ./scripts/setup_python_env.sh"
  exit 1
fi

if [[ ! -f "$ENV_FILE" && -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" && -z "${GCP_SA_KEY_B64:-}" && ! -f "$LOCAL_CREDENTIALS_DIR/service-account.json" ]]; then
  echo "Missing local credentials configuration."
  echo "Copy your JSON to:"
  echo "  $LOCAL_CREDENTIALS_DIR/service-account.json"
  echo "or copy .env.local.example to .env.local and set GOOGLE_APPLICATION_CREDENTIALS."
  exit 1
fi

"$VENV_PY" "$ROOT_DIR/scripts/export_tickets.py" "$@"
LATEST_EXPORT="$(cd "$ROOT_DIR" && ls -dt exports/tickets-* | head -n 1)"
"$VENV_PY" "$ROOT_DIR/scripts/analyze_tickets.py" --input-dir "$ROOT_DIR/$LATEST_EXPORT"

echo "Export directory: $ROOT_DIR/$LATEST_EXPORT"
echo "Dashboard: $ROOT_DIR/$LATEST_EXPORT/analysis/dashboard.html"
echo "Analysis report: $ROOT_DIR/$LATEST_EXPORT/analysis/report.md"
