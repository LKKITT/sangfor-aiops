"""深信服售后技术支持 Agent - 服务管理控制台 (Python GUI)"""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

# ============================================================
# 路径配置
# ============================================================
PROJECT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
BACKEND_DIR = PROJECT_DIR / "backend"
VENV_DIR = PROJECT_DIR / ".venv"
SCRIPTS_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "scripts" / "launcher-config.json"
VITE_CONFIG = FRONTEND_DIR / "vite.config.js"

DEFAULT_CFG = {"backend_host": "127.0.0.1", "backend_port": 8600, "frontend_host": "127.0.0.1", "frontend_port": 5173}
cfg = dict(DEFAULT_CFG)

# 进程跟踪
backend_process = None
frontend_process = None
_monitor_running = True


# ============================================================
# 配置读写
# ============================================================
def load_config():
    global cfg
    try:
        if CONFIG_FILE.exists():
            saved = json.loads(CONFIG_FILE.read_text("utf-8"))
            for k in DEFAULT_CFG:
                if k in saved:
                    cfg[k] = saved[k]
    except Exception:
        pass


def save_config():
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")


# ============================================================
# 同步 vite.config.js
# ============================================================
def sync_vite_config():
    if not VITE_CONFIG.exists():
        lbl_config_status.config(text="错误：找不到 vite.config.js", foreground="red")
        return False
    try:
        content = VITE_CONFIG.read_text("utf-8")
        target = f"http://{cfg['backend_host']}:{cfg['backend_port']}"
        changes = []

        m = re.search(r"target:\s*'[^']+'", content)
        if m:
            content = content.replace(m.group(0), f"target: '{target}'")
            changes.append(f"代理目标 → {target}")

        m = re.search(r"port:\s*\d+", content)
        if m:
            content = content.replace(m.group(0), f"port: {cfg['frontend_port']}")
            changes.append(f"前端端口 → {cfg['frontend_port']}")

        m = re.search(r"host:\s*'[^']+'", content)
        if m:
            content = content.replace(m.group(0), f"host: '{cfg['frontend_host']}'")
            changes.append(f"前端监听 → {cfg['frontend_host']}")

        VITE_CONFIG.write_text(content, "utf-8")
        if changes:
            lbl_config_status.config(text="✓ " + " | ".join(changes), foreground="green")
        else:
            lbl_config_status.config(text="警告：未找到需同步的字段", foreground="orange")
        return True
    except Exception as e:
        lbl_config_status.config(text=f"同步失败: {e}", foreground="red")
        return False


# ============================================================
# 进程管理
# ============================================================
def _kill_process_tree(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        os.kill(pid, signal.SIGTERM)


def _cleanup_residual():
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(proc.info["cmdline"] or [])
                if "uvicorn" in cmd.lower() and "app.main" in cmd:
                    _kill_process_tree(proc.info["pid"])
                if "vite" in cmd.lower():
                    _kill_process_tree(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        pass


def start_backend():
    global backend_process
    if backend_process and backend_process.poll() is None:
        lbl_status.config(text=f"后端已在运行 (PID: {backend_process.pid})")
        return True

    python_exe = VENV_DIR / "Scripts" / "python.exe"
    req_file = BACKEND_DIR / "requirements.txt"
    if not python_exe.exists():
        lbl_status.config(text="正在创建虚拟环境...")
        lbl_status.update()
        try:
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        except Exception as e:
            messagebox.showerror("后端启动失败", f"创建虚拟环境失败：{e}")
            return False

    # 依赖安装：官方源失败自动换清华镜像重试（弱网/首次部署 pip 常超时，
    # 静默失败曾导致 venv 半途而废、后端无法启动且无任何提示）
    lbl_status.config(text="正在安装/检查 Python 依赖（可能需要几分钟）...")
    lbl_status.update()
    pip_base = [str(python_exe), "-m", "pip", "install", "-r", str(req_file)]
    r = subprocess.run(pip_base, capture_output=True, text=True)
    if r.returncode != 0:
        lbl_status.config(text="官方源安装失败，改用清华镜像重试...")
        lbl_status.update()
        r = subprocess.run(pip_base + ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                           capture_output=True, text=True)
    if r.returncode != 0:
        tail = ((r.stderr or "") + (r.stdout or ""))[-600:]
        messagebox.showerror("后端依赖安装失败", f"pip 返回码 {r.returncode}（已尝试镜像重试）\n\n{tail}")
        return False

    # 依赖补装：以 requirements.txt 为准（含知识库技能依赖 requests/pycryptodome/
    # websocket-client），venv 已存在时也会检查缺失并自动安装
    lbl_status.config(text="正在核对 Python 依赖（requirements.txt）...")
    lbl_status.update()
    check_script = SCRIPTS_DIR / "check_deps.py"
    chk = subprocess.run([str(python_exe), str(check_script), "--quiet"], cwd=str(PROJECT_DIR),
                         capture_output=True, text=True)
    if chk.returncode != 0:
        tail = ((chk.stderr or "") + (chk.stdout or ""))[-600:]
        messagebox.showerror("后端依赖核对失败", f"check_deps 返回码 {chk.returncode}\n\n{tail}")
        return False

    cmd = [str(python_exe), "-m", "uvicorn", "app.main:app",
           "--host", cfg["backend_host"], "--port", str(cfg["backend_port"])]
    lbl_status.config(text=f"正在启动后端 {cfg['backend_host']}:{cfg['backend_port']}...")
    lbl_status.update()
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        backend_process = subprocess.Popen(
            cmd, cwd=str(BACKEND_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as e:
        messagebox.showerror("后端启动失败", f"无法启动后端进程：{e}")
        return False
    return True


def start_frontend():
    global frontend_process
    if frontend_process and frontend_process.poll() is None:
        lbl_status.config(text=f"前端已在运行 (PID: {frontend_process.pid})")
        return True

    if not (FRONTEND_DIR / "node_modules").exists():
        lbl_status.config(text="正在安装前端依赖（可能需要几分钟）...")
        lbl_status.update()
        r = subprocess.run(["npm", "install", "--no-fund", "--no-audit"],
                           cwd=str(FRONTEND_DIR), shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            lbl_status.config(text="npm 官方源失败，改用 npmmirror 镜像重试...")
            lbl_status.update()
            r = subprocess.run(["npm", "install", "--no-fund", "--no-audit",
                                "--registry=https://registry.npmmirror.com"],
                               cwd=str(FRONTEND_DIR), shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            tail = ((r.stderr or "") + (r.stdout or ""))[-600:]
            messagebox.showerror("前端依赖安装失败", f"npm 返回码 {r.returncode}（已尝试镜像重试）\n\n{tail}")
            return False

    lbl_status.config(text=f"正在启动前端 :{cfg['frontend_port']}...")
    lbl_status.update()
    env = os.environ.copy()
    env["PORT"] = str(cfg["frontend_port"])
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    if sys.platform == "win32":
        frontend_process = subprocess.Popen(
            ["cmd", "/c", "npm", "run", "dev"],
            cwd=str(FRONTEND_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    else:
        frontend_process = subprocess.Popen(
            ["npm", "run", "dev"], cwd=str(FRONTEND_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return True


def _open_browser(url):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass


def start_all():
    btn_start.config(state="disabled", text="启动中...")
    btn_start.update()
    try:
        apply_config()
        if not start_backend():
            return   # 失败详情已弹窗，按钮在 finally 复位
        start_frontend()
        root.after(3000, lambda: _open_browser(f"http://localhost:{cfg['frontend_port']}"))
    except Exception as e:
        messagebox.showerror("启动失败", f"启动过程出现未预期的错误：{e}")
    finally:
        btn_start.config(state="normal", text="启动全部")


def stop_all():
    global backend_process, frontend_process
    lbl_status.config(text="正在停止服务...")
    lbl_status.update()

    if frontend_process and frontend_process.poll() is None:
        _kill_process_tree(frontend_process.pid)
        frontend_process.wait(5)
    frontend_process = None

    if backend_process and backend_process.poll() is None:
        _kill_process_tree(backend_process.pid)
        backend_process.wait(5)
    backend_process = None

    _cleanup_residual()
    root.after(500, update_status)


def restart_all():
    btn_restart.config(state="disabled", text="重启中...")
    btn_restart.update()
    stop_all()
    time.sleep(2)
    start_all()
    btn_restart.config(state="normal", text="重启全部")


# ============================================================
# 配置应用
# ============================================================
def apply_config():
    host = entry_backend_host.get().strip()
    port = entry_backend_port.get().strip()
    fport = entry_frontend_port.get().strip()
    fhost = entry_frontend_host.get().strip()

    if not re.match(r'^[\d.]+$', host):
        lbl_config_status.config(text="错误：后端监听IP格式无效", foreground="red")
        entry_backend_host.config(bg="#FFD0D0")
        return False
    entry_backend_host.config(bg="white")

    if not (port.isdigit() and 1 <= int(port) <= 65535):
        lbl_config_status.config(text="错误：后端端口范围 1-65535", foreground="red")
        entry_backend_port.config(bg="#FFD0D0")
        return False
    entry_backend_port.config(bg="white")

    if not (fport.isdigit() and 1 <= int(fport) <= 65535):
        lbl_config_status.config(text="错误：前端端口范围 1-65535", foreground="red")
        entry_frontend_port.config(bg="#FFD0D0")
        return False
    entry_frontend_port.config(bg="white")

    if not re.match(r'^[\d.]+$', fhost):
        lbl_config_status.config(text="错误：前端监听IP格式无效", foreground="red")
        entry_frontend_host.config(bg="#FFD0D0")
        return False
    entry_frontend_host.config(bg="white")

    cfg["backend_host"] = host
    cfg["backend_port"] = int(port)
    cfg["frontend_host"] = fhost
    cfg["frontend_port"] = int(fport)

    save_config()
    sync_vite_config()
    return True


def restore_default_config():
    global cfg
    cfg = dict(DEFAULT_CFG)
    save_config()
    sync_vite_config()
    entry_backend_host.delete(0, tk.END)
    entry_backend_host.insert(0, cfg["backend_host"])
    entry_backend_port.delete(0, tk.END)
    entry_backend_port.insert(0, str(cfg["backend_port"]))
    entry_frontend_host.delete(0, tk.END)
    entry_frontend_host.insert(0, str(cfg.get("frontend_host", "127.0.0.1")))
    entry_frontend_port.delete(0, tk.END)
    entry_frontend_port.insert(0, str(cfg["frontend_port"]))
    lbl_config_status.config(text="已恢复默认配置", foreground="green")


# ============================================================
# 状态监控
# ============================================================
def update_status():
    if not _monitor_running:
        return

    b_run = backend_process is not None and backend_process.poll() is None
    f_run = frontend_process is not None and frontend_process.poll() is None

    b_ok = False
    if b_run:
        try:
            url = f"http://{cfg['backend_host']}:{cfg['backend_port']}/api/health"
            resp = urllib.request.urlopen(url, timeout=2)
            b_ok = resp.getcode() == 200
        except Exception:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.5)
                result = sock.connect_ex((cfg['backend_host'], cfg['backend_port']))
                sock.close()
                b_ok = result == 0
            except Exception:
                pass

    if b_run and b_ok:
        lbl_backend_status.config(text="● 运行中", foreground="green")
        lbl_status.config(text=f"后端: 运行中 {cfg['backend_host']}:{cfg['backend_port']} (PID: {backend_process.pid})")
    elif b_run:
        lbl_backend_status.config(text="○ 启动中", foreground="orange")
        lbl_status.config(text="后端: 启动中...")
    else:
        lbl_backend_status.config(text="○ 已停止", foreground="gray")

    if f_run:
        lbl_frontend_status.config(text=f"● 运行中 (PID: {frontend_process.pid})", foreground="green")
    else:
        lbl_frontend_status.config(text="○ 已停止", foreground="gray")

    lbl_backend_url.config(text=f"http://{cfg['backend_host']}:{cfg['backend_port']}/docs")
    lbl_frontend_url.config(text=f"http://localhost:{cfg['frontend_port']}")

    root.after(3000, update_status)


# ============================================================
# 构建 GUI
# ============================================================
def build_gui():
    global root, entry_backend_host, entry_backend_port, entry_frontend_port, entry_frontend_host
    global lbl_backend_status, lbl_frontend_status, lbl_status
    global lbl_backend_url, lbl_frontend_url, lbl_config_status
    global btn_start, btn_restart

    root = tk.Tk()
    root.title("深信服售后技术支持 Agent - 服务管理控制台")
    root.geometry("560x520+{}+{}".format(
        (root.winfo_screenwidth() - 560) // 2,
        (root.winfo_screenheight() - 520) // 2))
    root.resizable(False, False)

    # ===== 标题 =====
    ttk.Label(root, text="深信服售后技术支持 Agent",
              font=("Microsoft YaHei", 14, "bold")).pack(pady=(15, 0))
    ttk.Label(root, text="服务管理控制台",
              font=("Microsoft YaHei", 9), foreground="gray").pack()

    # ===== 服务状态 =====
    f_status = ttk.LabelFrame(root, text="服务状态", padding=10)
    f_status.pack(fill="x", padx=20, pady=(10, 0))

    r0 = ttk.Frame(f_status)
    r0.pack(fill="x", pady=2)
    tk.Label(r0, text="后端 API:", font=("Microsoft YaHei", 9, "bold"), width=9, anchor="w").pack(side="left")
    lbl_backend_status = tk.Label(r0, text="○ 已停止", foreground="gray", width=12, anchor="w")
    lbl_backend_status.pack(side="left")
    lbl_backend_url = tk.Label(r0, text=f"http://{cfg['backend_host']}:{cfg['backend_port']}/docs",
                               foreground="royalblue", cursor="hand2")
    lbl_backend_url.pack(side="left", padx=(10, 0))
    lbl_backend_url.bind("<Button-1>", lambda e: _open_browser(lbl_backend_url.cget("text")))

    r1 = ttk.Frame(f_status)
    r1.pack(fill="x", pady=2)
    tk.Label(r1, text="前端界面:", font=("Microsoft YaHei", 9, "bold"), width=9, anchor="w").pack(side="left")
    lbl_frontend_status = tk.Label(r1, text="○ 已停止", foreground="gray", width=12, anchor="w")
    lbl_frontend_status.pack(side="left")
    lbl_frontend_url = tk.Label(r1, text=f"http://localhost:{cfg['frontend_port']}",
                                foreground="royalblue", cursor="hand2")
    lbl_frontend_url.pack(side="left", padx=(10, 0))
    lbl_frontend_url.bind("<Button-1>", lambda e: _open_browser(lbl_frontend_url.cget("text")))

    # ===== 操作按钮 =====
    f_actions = ttk.LabelFrame(root, text="操作", padding=10)
    f_actions.pack(fill="x", padx=20, pady=(8, 0))

    bf = ttk.Frame(f_actions)
    bf.pack()

    btn_start = tk.Button(bf, text="启动全部", font=("Microsoft YaHei", 9, "bold"),
                          bg="#2E8B57", fg="white", width=12, command=start_all)
    btn_start.pack(side="left", padx=4)

    btn_stop = tk.Button(bf, text="停止全部", font=("Microsoft YaHei", 9, "bold"),
                         bg="#DC143C", fg="white", width=12, command=stop_all)
    btn_stop.pack(side="left", padx=4)

    btn_restart = tk.Button(bf, text="重启全部", font=("Microsoft YaHei", 9, "bold"),
                            bg="#FF8C00", fg="white", width=12, command=restart_all)
    btn_restart.pack(side="left", padx=4)

    btn_browser = tk.Button(bf, text="打开前端", width=12,
                            command=lambda: _open_browser(f"http://localhost:{cfg['frontend_port']}"))
    btn_browser.pack(side="left", padx=4)

    # ===== 监听配置 =====
    f_config = ttk.LabelFrame(root, text="监听配置", padding=10)
    f_config.pack(fill="x", padx=20, pady=(8, 0))

    rc0 = ttk.Frame(f_config)
    rc0.pack(fill="x", pady=3)
    tk.Label(rc0, text="后端监听 IP:", width=10, anchor="w").pack(side="left")
    entry_backend_host = tk.Entry(rc0, width=18, relief="sunken", bd=1)
    entry_backend_host.insert(0, cfg["backend_host"])
    entry_backend_host.pack(side="left", padx=(0, 15))

    tk.Label(rc0, text="后端端口:", anchor="w").pack(side="left")
    entry_backend_port = tk.Entry(rc0, width=10, relief="sunken", bd=1)
    entry_backend_port.insert(0, str(cfg["backend_port"]))
    entry_backend_port.pack(side="left", padx=(5, 0))

    rc1 = ttk.Frame(f_config)
    rc1.pack(fill="x", pady=3)
    tk.Label(rc1, text="前端监听 IP:", width=10, anchor="w").pack(side="left")
    entry_frontend_host = tk.Entry(rc1, width=18, relief="sunken", bd=1)
    entry_frontend_host.insert(0, str(cfg.get("frontend_host", "127.0.0.1")))
    entry_frontend_host.pack(side="left", padx=(0, 15))

    tk.Label(rc1, text="前端端口:", anchor="w").pack(side="left")
    entry_frontend_port = tk.Entry(rc1, width=10, relief="sunken", bd=1)
    entry_frontend_port.insert(0, str(cfg["frontend_port"]))
    entry_frontend_port.pack(side="left", padx=(0, 15))

    btn_apply = tk.Button(rc1, text="应用配置", bg="#4682B4", fg="white", command=apply_config)
    btn_apply.pack(side="left", padx=4)

    btn_restore = tk.Button(rc1, text="恢复默认", command=restore_default_config)
    btn_restore.pack(side="left", padx=4)

    lbl_config_status = tk.Label(f_config, text="修改配置后点击「应用配置」保存并同步到前端代理",
                                  font=("Microsoft YaHei", 8), foreground="gray", anchor="w")
    lbl_config_status.pack(fill="x", pady=(3, 0))

    # ===== 状态栏 =====
    lbl_status = tk.Label(root, text="就绪", font=("Microsoft YaHei", 8), foreground="dimgray", anchor="w")
    lbl_status.pack(fill="x", padx=20, pady=(10, 0))

    # ===== 窗口关闭 =====
    def on_closing():
        global _monitor_running
        _monitor_running = False
        b = backend_process is not None and backend_process.poll() is None
        f = frontend_process is not None and frontend_process.poll() is None
        if b or f:
            resp = messagebox.askyesnocancel("确认退出", "服务正在运行，关闭前是否停止服务？")
            if resp is None:
                return
            if resp:
                stop_all()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    return root


# ============================================================
# 检查依赖
# ============================================================
def ensure_deps():
    try:
        import psutil  # noqa
    except ImportError:
        python_exe = VENV_DIR / "Scripts" / "python.exe"
        if python_exe.exists():
            subprocess.run([str(python_exe), "-m", "pip", "install", "-q", "psutil"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "psutil"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    ensure_deps()
    load_config()
    root = build_gui()
    root.after(500, update_status)
    root.mainloop()