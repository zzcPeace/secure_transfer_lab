#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m sft serve --config "${1:-runtime/server/server.toml}"
