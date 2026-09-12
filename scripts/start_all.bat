@echo off
title Sangfor Support Agent - One-Click Start
cd /d "%~dp0.."

echo ============================================
echo   Sangfor Support Agent - One-Click Start
echo ============================================

if not exist .venv (
  echo [1/4] Creating Python venv ...
  python -m venv .venv
  call .venv\Scripts\python -m pip install -q -r backend\requirements.txt
)

echo [2/4] Checking Python dependencies (requirements.txt) ...
if exist .venv\Scripts\python.exe (
  call .venv\Scripts\python.exe scripts\check_deps.py
) else (
  call python scripts\check_deps.py
)

if not exist backend\.env (
  echo [3/4] Creating backend\.env - please fill in LLM_API_KEY later
  copy backend\.env.example backend\.env >nul
)

echo [4/4] Starting backend :8600 and frontend :5173 (detached, no extra windows) ...
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8600' -WorkingDirectory 'backend'"
cd frontend
if not exist node_modules (
  echo        Installing frontend dependencies (first run, may take a few minutes) ...
  call npm install --no-fund --no-audit
)
cd ..
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'cmd.exe' -ArgumentList '/c','npm run dev' -WorkingDirectory 'frontend'"

echo Waiting for services to come up ...
timeout /t 6 /nobreak >nul
start "" http://localhost:5173

echo.
echo Started: frontend http://localhost:5173   backend http://127.0.0.1:8600/docs
echo To customize listen IPs/ports, run: scripts\launcher.py   To stop: close python/node processes
pause
