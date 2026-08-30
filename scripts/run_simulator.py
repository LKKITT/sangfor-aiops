"""独立运行 AF 模拟器（可选）：python scripts/run_simulator.py [--port 8001]

用于将模拟器作为独立进程供多端联调；后端默认已内置进程内模拟器，无需运行本脚本。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import uvicorn  # noqa: E402

from app.adapters.factory import create_simulator_app  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    print(f"AF 模拟器运行于 http://{args.host}:{args.port} （账号 admin / Sangfor@123）")
    uvicorn.run(create_simulator_app(), host=args.host, port=args.port)
