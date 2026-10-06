# -*- coding: utf-8 -*-
"""复现 launcher 启动方式（CREATE_NO_WINDOW + DEVNULL）验证 MCP 是否挂起。"""
import subprocess
import sys
import time

import requests

flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
p = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8602"],
    cwd=".", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=flags)
print("backend pid", p.pid, "on 8602")
time.sleep(8)
try:
    payload = {"name": "acheck-readonly", "transport": "stdio",
               "command": "C:/Users/LIAOM/.workbuddy/binaries/python/envs/default/Scripts/python.exe",
               "args": ["D:/纪元平台_v6.0.3_full_1218/mcp-server/server.py"],
               "env": {"ACHECK_ROOT": "D:/纪元平台_v6.0.3_full_1218"}}
    t0 = time.time()
    r = requests.post("http://127.0.0.1:8602/api/settings/mcp/test", json=payload, timeout=90)
    print(f"{r.status_code} ({time.time()-t0:.1f}s)", r.text[:200])
finally:
    p.terminate()
