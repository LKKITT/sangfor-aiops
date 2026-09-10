@echo off
chcp 65001 >nul
title ���ŷ� Agent - �����������̨

cd /d "%~dp0"

echo �������������������̨...

if exist "..\.venv\Scripts\python.exe" (
    "..\.venv\Scripts\python.exe" launcher.py
) else (
    python launcher.py
)

if %errorlevel% neq 0 (
    echo.
    echo ����ʧ�ܣ���ȷ�� Python �Ѱ�װ���ɷ��ʡ�
    pause
)
