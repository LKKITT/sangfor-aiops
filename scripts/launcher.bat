@echo off
chcp 65001 >nul
title 深信服 Agent - 服务管理控制台

cd /d "%~dp0"

echo 正在启动服务管理控制台...

if exist "..\.venv\Scripts\python.exe" (
    "..\.venv\Scripts\python.exe" launcher.py
) else (
    python launcher.py
)

if %errorlevel% neq 0 (
    echo.
    echo 启动失败，请确保 Python 已安装并可访问。
    pause
)
