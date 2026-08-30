#!/usr/bin/env bash
# 深信服售后技术支持 Agent - 一键启动（Linux/macOS）
set -e
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "[1/3] 创建 Python 虚拟环境..."
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r backend/requirements.txt
fi
if [ ! -f backend/.env ]; then
  echo "[2/3] 生成 backend/.env（请按需填入 LLM_API_KEY）"
  cp backend/.env.example backend/.env
fi

echo "[3/3] 启动后端(8600)与前端(5173)..."
(cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8600 &) 
(cd frontend && { [ -d node_modules ] || npm install --no-fund --no-audit; } && npm run dev &)

sleep 5
echo "启动完成：前端 http://localhost:5173  后端 http://localhost:8600/docs"
