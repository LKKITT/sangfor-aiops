@echo off
chcp 65001 >nul
title Sangfor Support Agent
echo ============================================
echo   深信服售后技术支持 Agent - 一键启动
echo ============================================
cd /d "%~dp0.."

if not exist .venv (
  echo [1/4] 创建 Python 虚拟环境...
  python -m venv .venv
  call .venv\Scripts\python -m pip install -q -r backend\requirements.txt
)
echo [2/4] 检查并补装 Python 依赖（含知识库技能依赖，已有环境也会检查）...
if exist .venv\Scripts\python.exe (
  call .venv\Scripts\python.exe scripts\check_deps.py
) else (
  call python scripts\check_deps.py
)
if not exist backend\.env (
  echo [3/4] 生成 backend\.env（请按需填入 LLM_API_KEY）
  copy backend\.env.example backend\.env >nul
)

echo [4/4] 启动后端(8600)与前端(5173)...
start "sangfor-agent-backend" cmd /c "cd backend && ..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8600"
start "sangfor-agent-frontend" cmd /c "cd frontend && (if not exist node_modules (call npm install --no-fund --no-audit)) && call npm run dev"

timeout /t 5 >nul
start http://localhost:5173
echo.
echo 启动完成：前端 http://localhost:5173  后端 http://localhost:8600/docs
echo 关闭请直接关闭弹出的两个窗口。
pause
