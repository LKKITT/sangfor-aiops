---
name: windows-quickstart
description: 在全新 Windows 电脑上从零部署并运行本项目（sangfor-agent 售后技术支持 Agent）。当需要在新的 Windows 机器上初始化环境、安装 Python/Node 依赖、启动前后端服务、配置 LLM Key，或排查"跑不起来/启动失败"类环境问题时使用。
---

# Windows 全新环境快速部署

目标：在一台没有任何开发环境的 Windows 10/11 电脑上，把本项目跑起来并在浏览器中可用。

## 环境要求

| 组件 | 版本要求 | 用途 |
|------|---------|------|
| Python | 3.10+（推荐 3.12） | 后端 FastAPI / uvicorn，端口 8600 |
| Node.js | 18+（LTS） | 前端 Vite + Vue3，端口 5173 |
| winget | Win10/11 一般自带 | 自动安装上述组件；缺失时按脚本提示手动安装 |

## 一键部署脚本（推荐入口）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Mirror
```

脚本幂等，可重复执行；本身只依赖系统自带 PowerShell（全新机器无需预装 Python/Node）。

| 参数 | 作用 |
|------|------|
| `-Mirror` | 国内镜像加速（pip 清华源 + npm npmmirror），国内网络建议加 |
| `-Launch` | 初始化完成后立即调用 `scripts\start_all.bat` 启动服务 |

脚本行为：检测/安装 Python 与 Node → 创建 `.venv` → 安装 `backend/requirements.txt` → `npm install` → 生成 `backend\.env`（提示填 `LLM_API_KEY`）。

## 部署步骤（Agent 执行清单）

1. **探测现状**：`python --version`（或 `py -3 --version`）、`node --version`；检查 `.venv\`、`frontend\node_modules\`、`backend\.env` 是否存在。
2. **执行部署**：运行上面的部署脚本。按用户网络环境决定是否加 `-Mirror`；已有部分环境时脚本自动跳过已完成步骤。
3. **配置 LLM Key**：脚本生成 `backend\.env` 后提醒用户编辑填入 `LLM_API_KEY`（智谱 / DeepSeek 等 OpenAI 兼容接口均可，`LLM_MODEL` 同步修改）。也可引导用户启动后在界面左下角「平台设置」中配置，保存即生效。
4. **启动服务**：`scripts\start_all.bat`（一键分离启动前后端并打开浏览器）或 `python scripts\launcher.py`（GUI 控制台，可自定义监听 IP/端口）。
5. **验证**：
   - 后端健康检查：`curl http://127.0.0.1:8600/api/health` 返回正常 JSON
   - 前端：浏览器打开 `http://localhost:5173`，默认已连接「演示-AF模拟器」设备，可直接对话
   - 后端 API 文档：`http://127.0.0.1:8600/docs`

## 常见问题排查

| 现象 | 处理 |
|------|------|
| 无 winget（旧版系统） | 按脚本输出的官网链接手动安装 Python / Node，装完重开 PowerShell 再跑脚本 |
| 敲 `python` 弹出微软商店 | WindowsApps 商店占位符，脚本会自动改用 `py -3`；手动场景可重装 Python 并勾选 "Add python.exe to PATH" |
| winget 安装 Node 提示权限不足 | 以管理员身份重开 PowerShell 再运行脚本 |
| pip / npm 超时或慢 | 加 `-Mirror` 参数；或手动设置 `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple`、`npm --registry=https://registry.npmmirror.com` |
| 端口 8600 / 5173 被占用 | 用 `python scripts\launcher.py` 修改监听端口（保存后自动同步前端代理配置） |
| Node < 18 导致 vite 启动失败 | 升级 Node LTS 后删除 `frontend\node_modules` 重新 `npm install` |
| 刚安装完 python/node 但当前窗口找不到 | PATH 未刷新：重开 PowerShell 再执行（脚本已内置注册表级 PATH 刷新，多数情况无需重开） |

## 其他说明

- **只读模式**：`backend\.env` 中 `READONLY_MODE=true` 可让 Agent 拒绝一切变更类操作，演示/评估场景建议开启。
- **停止服务**：关闭启动产生的 python / node 进程，或用 `launcher.py` 的停止按钮。
- **模拟器**：不接真实设备也能完整体验，默认设备「演示-AF模拟器」即内置模拟器（`backend/app/adapters/simulator/`）。
