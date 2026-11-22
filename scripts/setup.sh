#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v uv >/dev/null 2>&1; then
  if [[ ! -e .venv ]]; then
    uv venv --python python3.12 .venv
  fi
  uv pip install --python .venv/bin/python -c requirements.txt -e '.[gui]'
else
  python3.12 -m venv .venv
  .venv/bin/python -m pip install -c requirements.txt -e '.[gui]'
fi
if [[ ! -e runtime ]]; then
  .venv/bin/python -m sft init-demo --dir runtime
fi
printf '%s\n' 'Python 依赖和实验配置已准备，正在检查端口与图形环境：'
if .venv/bin/python -m sft doctor; then
  printf '%s\n' '服务端：bash scripts/start-server.sh；客户端：bash scripts/start-client.sh'
else
  printf '%s\n' '存在上述启动条件问题，请按提示处理；没有图形桌面时仍可运行服务端和 CLI。'
fi
