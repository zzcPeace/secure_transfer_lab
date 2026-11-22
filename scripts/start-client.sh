#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m sft gui --profile "${1:-runtime/clients/alice/client.json}"
