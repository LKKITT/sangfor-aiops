"""依赖检查与补装：以 backend/requirements.txt 为唯一事实源。

所有启动入口（start_all.bat / launcher.py / launch.ps1）在启动后端前调用本脚本：
逐条尝试导入 requirements.txt 中的依赖，缺失时自动 pip 补装。
修复「venv 已存在导致新增依赖（含知识库技能依赖 requests/pycryptodome/websocket-client）
永不安装」的问题。

用法：
    python scripts/check_deps.py            # 检查并自动补装（默认）
    python scripts/check_deps.py --check    # 仅检查，缺失时退出码 1，不安装
    python scripts/check_deps.py --quiet    # 静默模式（GUI 启动器调用）
"""
import importlib
import re
import subprocess
import sys
from pathlib import Path

REQ_FILE = Path(__file__).resolve().parent.parent / "backend" / "requirements.txt"

# 包名（小写）→ 导入名：仅登记包名与导入名不一致的条目
IMPORT_MAP = {
    "python-dotenv": "dotenv",
    "pycryptodome": "Crypto",
    "websocket-client": "websocket",
    "pytest-asyncio": "pytest_asyncio",
    "wecom-aibot-python-sdk": "aibot",
}


def _req_lines() -> list[str]:
    lines = []
    for raw in REQ_FILE.read_text("utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            line = line.split(" #", 1)[0].strip()   # 剥行内注释（如 mcp>=1.2,<2  # 锁定说明）
            if line:
                lines.append(line)
    return lines


def _pkg_name(line: str) -> str:
    m = re.match(r"[A-Za-z0-9_.\-]+", line)
    name = m.group(0) if m else line
    return name.split("[")[0].lower()


def _import_name(pkg: str) -> str:
    return IMPORT_MAP.get(pkg, pkg.replace("-", "_"))


def find_missing() -> list[str]:
    """返回 requirements.txt 中当前环境导入失败的条目。"""
    missing = []
    for line in _req_lines():
        mod = _import_name(_pkg_name(line))
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(line)
        except Exception:   # noqa: BLE001 —— 已安装但导入异常：不重复安装，交由运行时暴露
            pass
    return missing


def install(pkgs: list[str], quiet: bool = False) -> bool:
    if not pkgs:
        return True
    if not quiet:
        print(f"[deps] 缺失依赖：{', '.join(pkgs)}")
        print("[deps] 正在自动安装...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs])
    if r.returncode != 0:
        # 官方源超时/失败自动换清华镜像重试（弱网环境单次安装常超时）
        if not quiet:
            print("[deps] 官方源安装失败，改用清华镜像重试...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                            "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", *pkgs])
    return r.returncode == 0


def main() -> int:
    quiet = "--quiet" in sys.argv
    check_only = "--check" in sys.argv
    if not REQ_FILE.exists():
        if not quiet:
            print(f"[deps] 未找到 {REQ_FILE}")
        return 2
    missing = find_missing()
    if not missing:
        if not quiet:
            print("[deps] 依赖完整（含知识库技能依赖 requests/pycryptodome/websocket-client）")
        return 0
    if check_only:
        if not quiet:
            print(f"[deps] 缺失 {len(missing)} 项：{', '.join(missing)}")
        return 1
    if not install(missing, quiet):
        if not quiet:
            print("[deps] 安装失败，请手动执行：pip install -r backend/requirements.txt")
        return 1
    still = find_missing()
    if not quiet:
        print("[deps] 依赖已就绪" if not still else f"[deps] 仍有缺失：{', '.join(still)}")
    return 0 if not still else 1


if __name__ == "__main__":
    sys.exit(main())
