#!/usr/bin/env bash
# 一键启动：创建虚拟环境 → 安装依赖 → 首次运行构建前端 → 起服务
# 用法：./run.sh             默认 8000 端口
#       PORT=9000 ./run.sh   指定端口
# macOS / Linux / Windows Git Bash 都可用
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# 找一个可用的解释器（Windows Git Bash 通常只有 python，没有 python3）
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "✗ 没有找到 Python，请先安装 3.9+（推荐 3.11）"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "→ 创建虚拟环境 .venv"
  "$PY" -m venv .venv
fi

# 跨平台激活：Linux/macOS 在 bin/，Windows 在 Scripts/
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
  # shellcheck disable=SC1091
  . .venv/Scripts/activate
else
  echo "✗ 虚拟环境不完整，请删除 .venv 目录后重试"
  exit 1
fi

echo "→ 安装后端依赖"
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
if python -m pip install -q -r requirements-optional.txt; then
  echo "  ViennaRNA 已就绪（真实 kcal/mol 能量）"
else
  echo "  ⚠ ViennaRNA 装不上，后端会自动降级到纯 Python 引擎（功能可用，能量为估计值）"
fi

if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "→ 首次运行，构建前端（约 1–2 分钟）"
    ( cd frontend && npm install --no-fund --no-audit && npm run build )
  else
    echo "⚠ 没检测到 npm，跳过前端构建。装好 Node.js 18+ 后重跑本脚本即可；"
    echo "  现在也可以只用 API：http://127.0.0.1:$PORT/docs"
  fi
fi

echo "→ 启动服务：http://127.0.0.1:$PORT  （Ctrl+C 停止）"
exec python -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
