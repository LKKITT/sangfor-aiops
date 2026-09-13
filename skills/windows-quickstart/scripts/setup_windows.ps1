#Requires -Version 5.1
<#
.SYNOPSIS
    sangfor-agent 全新 Windows 环境一键部署脚本。

.DESCRIPTION
    在全新 Windows 10/11 电脑上从零准备本项目运行环境：
    检测/安装 Python 3.10+ 与 Node.js 18+（优先经 winget 自动安装）、
    创建 .venv 并安装后端依赖（backend/requirements.txt）、
    安装前端依赖（npm install）、生成 backend/.env。
    幂等可重复执行：已就绪的步骤自动跳过。

    本脚本本身不依赖 Python/Node —— 用系统自带 PowerShell 即可引导全新机器。

.PARAMETER Mirror
    使用国内镜像（pip 清华源 + npm npmmirror），国内网络环境建议添加。

.PARAMETER Launch
    初始化完成后立即运行 scripts\start_all.bat 启动前后端服务。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Mirror

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Launch
#>
param(
    [switch]$Mirror,
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
# 脚本位于 skills/windows-quickstart/scripts/ 下，项目根为其上三级
$PROJECT_DIR = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

function Refresh-Path {
    # winget 安装后刷新当前会话 PATH（合并机器级 + 用户级）
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Get-PythonInfo {
    # 返回 @{ Cmd = string[]; Ver = version } 或 $null。
    # 优先 py 启动器（跟随最新 3.x）；WindowsApps 商店占位符无版本输出，自动排除。
    foreach ($p in @(@("py", "-3"), @("python"))) {
        try {
            if ($p.Count -gt 1) { $out = [string](& $p[0] $p[1] --version 2>$null) }
            else                { $out = [string](& $p[0] --version 2>$null) }
            if ($out -match "Python\s+(3\.\d+(\.\d+)?)") {
                return @{ Cmd = $p; Ver = [version]$Matches[1] }
            }
        } catch { }
    }
    return $null
}

function Get-NodeVersion {
    try {
        $out = [string](node --version 2>$null)
        if ($out -match "(\d+\.\d+(\.\d+)?)") { return [version]$Matches[1] }
    } catch { }
    return $null
}

function Invoke-WingetInstall([string]$Id, [string]$Name) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return }
    Write-Host "[..] 经 winget 安装 $Name ($Id)，可能需要几分钟 ..."
    try {
        winget install --id $Id -e --silent `
            --accept-source-agreements --accept-package-agreements | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "winget 安装 $Name 返回码 $LASTEXITCODE（若提示权限不足，请以管理员身份重开 PowerShell 后重跑本脚本）"
        }
    } catch {
        Write-Warning "winget 安装 $Name 失败：$_"
    }
    Refresh-Path
}

Write-Host "=============================================="
Write-Host "  sangfor-agent Windows 环境初始化"
Write-Host "  项目目录: $PROJECT_DIR"
Write-Host "=============================================="

# ---------- 1. winget 可用性 ----------
$hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
if (-not $hasWinget) {
    Write-Warning "未检测到 winget，缺失组件需手动安装："
    Write-Host "  Python 3.12 : https://www.python.org/downloads/  （务必勾选 Add python.exe to PATH）"
    Write-Host "  Node.js LTS : https://nodejs.org/zh-cn"
    Write-Host "  安装后重开 PowerShell，再运行本脚本。"
}

# ---------- 2. Python 3.10+ ----------
$py = Get-PythonInfo
if ($py -and $py.Ver -ge [version]"3.10") {
    Write-Host ("[OK] Python {0} 已安装（{1}）" -f $py.Ver, ($py.Cmd -join " "))
} else {
    if ($py) { Write-Warning "Python $($py.Ver) 版本过低（需 3.10+），将安装 Python 3.12" }
    else     { Write-Host   "[..] 未检测到可用的 Python，安装 Python 3.12 ..." }
    if ($hasWinget) { Invoke-WingetInstall "Python.Python.3.12" "Python 3.12" }
    $py = Get-PythonInfo
    if (-not $py -or $py.Ver -lt [version]"3.10") {
        Write-Warning "安装后当前会话仍找不到 Python 3.10+。请关闭本窗口，重新打开 PowerShell 后再次运行本脚本。"
        exit 1
    }
    Write-Host ("[OK] Python {0} 安装完成" -f $py.Ver)
}

# ---------- 3. Node.js 18+ ----------
$node = Get-NodeVersion
if ($node -and $node -ge [version]"18.0") {
    Write-Host "[OK] Node.js $node 已安装"
} else {
    if ($node) { Write-Warning "Node.js $node 版本过低（Vite 5 需 18+），将安装 LTS 版" }
    else       { Write-Host   "[..] 未检测到 Node.js，安装 LTS 版 ..." }
    if ($hasWinget) { Invoke-WingetInstall "OpenJS.NodeJS.LTS" "Node.js LTS" }
    $node = Get-NodeVersion
    if (-not $node -or $node -lt [version]"18.0") {
        Write-Warning "安装后当前会话仍找不到 Node.js 18+。请关闭本窗口，重新打开 PowerShell 后再次运行本脚本。"
        exit 1
    }
    Write-Host "[OK] Node.js $node 安装完成"
}

# ---------- 4. 后端虚拟环境与依赖 ----------
$venvDir = Join-Path $PROJECT_DIR ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[..] 创建虚拟环境 .venv ..."
    if ($py.Cmd.Count -gt 1) { & $py.Cmd[0] $py.Cmd[1] -m venv $venvDir }
    else                     { & $py.Cmd[0] -m venv $venvDir }
} else {
    Write-Host "[OK] 虚拟环境 .venv 已存在"
}

Write-Host "[..] 安装后端依赖（backend/requirements.txt）..."
$pipArgs = @("-m", "pip", "install", "-q",
             "-r", (Join-Path $PROJECT_DIR "backend\requirements.txt"))
if ($Mirror) { $pipArgs += @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple") }
& $venvPython @pipArgs
if ($LASTEXITCODE -ne 0) { Write-Warning "后端依赖安装返回码 $LASTEXITCODE，请检查上方输出" }
else                     { Write-Host "[OK] 后端依赖安装完成" }

# ---------- 5. 前端依赖 ----------
$frontendDir = Join-Path $PROJECT_DIR "frontend"
if (Test-Path (Join-Path $frontendDir "node_modules")) {
    Write-Host "[OK] 前端依赖已安装（node_modules 存在）"
} else {
    Write-Host "[..] 安装前端依赖（首次较慢）..."
    Push-Location $frontendDir
    try {
        if ($Mirror) { npm install --no-fund --no-audit --registry=https://registry.npmmirror.com }
        else         { npm install --no-fund --no-audit }
        if ($LASTEXITCODE -ne 0) { Write-Warning "npm install 返回码 $LASTEXITCODE，请检查上方输出" }
        else                     { Write-Host "[OK] 前端依赖安装完成" }
    } finally {
        Pop-Location
    }
}

# ---------- 6. 生成 backend/.env ----------
$envFile = Join-Path $PROJECT_DIR "backend\.env"
if (Test-Path $envFile) {
    Write-Host "[OK] backend\.env 已存在"
} else {
    Copy-Item (Join-Path $PROJECT_DIR "backend\.env.example") $envFile
    Write-Warning "已生成 backend\.env —— 启动前请编辑并填入 LLM_API_KEY（智谱 / DeepSeek 等 OpenAI 兼容 Key）"
}

# ---------- 7. 完成提示 ----------
Write-Host ""
Write-Host "=============================================="
Write-Host "  环境就绪！下一步："
Write-Host "    1. 编辑 backend\.env，填入 LLM_API_KEY"
Write-Host "    2. 运行 scripts\start_all.bat 启动前后端"
Write-Host "    3. 浏览器打开 http://localhost:5173"
Write-Host "       （默认已连接『演示-AF模拟器』设备）"
Write-Host "=============================================="

if ($Launch) {
    Write-Host "[..] 启动服务（scripts\start_all.bat）..."
    & (Join-Path $PROJECT_DIR "scripts\start_all.bat")
}
