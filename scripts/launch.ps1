#Requires -Version 5.1
<#
.SYNOPSIS
    深信服售后技术支持 Agent - 服务管理控制台
.DESCRIPTION
    提供图形界面一键启动/停止/重启前后端服务，修改监听端口IP，
    监视服务运行状态，恢复默认配置。
#>
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ============================================================
# 配置
# ============================================================
$PROJECT_DIR = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$FRONTEND_DIR = Join-Path $PROJECT_DIR "frontend"
$BACKEND_DIR = Join-Path $PROJECT_DIR "backend"
$VENV_DIR = Join-Path $PROJECT_DIR ".venv"
$CONFIG_FILE = Join-Path $PROJECT_DIR "scripts\launcher-config.json"
$VITE_CONFIG = Join-Path $FRONTEND_DIR "vite.config.js"

# 默认配置
$DEFAULT_CFG = @{
    backend_host = "127.0.0.1"
    backend_port = 8600
    frontend_port = 5173
}

# 当前配置（从文件加载或默认）
$cfg = @{}
$cfg.BackendHost = $DEFAULT_CFG.backend_host
$cfg.BackendPort = $DEFAULT_CFG.backend_port
$cfg.FrontendPort = $DEFAULT_CFG.frontend_port

# 进程 PID 跟踪
$script:backend_pid = $null
$script:frontend_pid = $null
$script:backend_proc = $null
$script:frontend_proc = $null

# ============================================================
# 配置读写
# ============================================================
function Load-Config {
    if (Test-Path $CONFIG_FILE) {
        try {
            $saved = Get-Content $CONFIG_FILE -Raw -Encoding UTF8 | ConvertFrom-Json
            $cfg.BackendHost = if ($saved.backend_host) { $saved.backend_host } else { $DEFAULT_CFG.backend_host }
            $cfg.BackendPort = if ($saved.backend_port) { [int]$saved.backend_port } else { $DEFAULT_CFG.backend_port }
            $cfg.FrontendPort = if ($saved.frontend_port) { [int]$saved.frontend_port } else { $DEFAULT_CFG.frontend_port }
        } catch { }  # 出错则使用默认值
    }
}

function Save-Config {
    $config = @{
        backend_host  = $cfg.BackendHost
        backend_port  = $cfg.BackendPort
        frontend_port = $cfg.FrontendPort
    }
    $config | ConvertTo-Json -Compress | Set-Content $CONFIG_FILE -Encoding UTF8
}

function Restore-DefaultConfig {
    $cfg.BackendHost = $DEFAULT_CFG.backend_host
    $cfg.BackendPort = $DEFAULT_CFG.backend_port
    $cfg.FrontendPort = $DEFAULT_CFG.frontend_port
    Save-Config
    Sync-ViteConfig
    $txtBackendHost.Text = $cfg.BackendHost
    $txtBackendPort.Text = $cfg.BackendPort
    $txtFrontendPort.Text = $cfg.FrontendPort
    $txtBackendHost.BackColor = [System.Drawing.Color]::White
    $txtBackendPort.BackColor = [System.Drawing.Color]::White
    $txtFrontendPort.BackColor = [System.Drawing.Color]::White
    $lblConfigStatus.Text = "已恢复默认配置"
    $lblConfigStatus.ForeColor = [System.Drawing.Color]::Green
}

# ============================================================
# 同步 vite.config.js 的代理目标端口
# ============================================================
function Sync-ViteConfig {
    if (-not (Test-Path $VITE_CONFIG)) {
        $lblConfigStatus.Text = "错误：找不到 vite.config.js"
        $lblConfigStatus.ForeColor = [System.Drawing.Color]::Red
        return $false
    }
    try {
        $content = Get-Content $VITE_CONFIG -Raw -Encoding UTF8
        $changes = @()

        # 同步代理目标端口
        $target = "http://$($cfg.BackendHost):$($cfg.BackendPort)"
        if ($content -match "target:\s*'[^']+'") {
            $content = $content -replace "target:\s*'[^']+'", "target: '$target'"
            $changes += "代理目标 → $target"
        }

        # 同步前端端口
        if ($content -match "port:\s*\d+") {
            $content = $content -replace "port:\s*\d+", "port: $($cfg.FrontendPort)"
            $changes += "前端端口 → $($cfg.FrontendPort)"
        }

        Set-Content $VITE_CONFIG $content -Encoding UTF8 -NoNewline
        if ($changes.Count -gt 0) {
            $lblConfigStatus.Text = "✓ $($changes -join ' | ')"
            $lblConfigStatus.ForeColor = [System.Drawing.Color]::Green
        } else {
            $lblConfigStatus.Text = "警告：vite.config.js 中未找到需要同步的字段"
            $lblConfigStatus.ForeColor = [System.Drawing.Color]::Orange
        }
        return $true
    } catch {
        $lblConfigStatus.Text = "错误：同步 vite.config.js 失败 - $_"
        $lblConfigStatus.ForeColor = [System.Drawing.Color]::Red
        return $false
    }
}

# ============================================================
# 进程管理
# ============================================================
function Get-ProcessByPid {
    param([int]$TargetPid)
    if (-not $TargetPid) { return $null }
    try { $p = Get-Process -Id $TargetPid -ErrorAction Stop; if (-not $p.HasExited) { return $p } } catch {}
    return $null
}

function Start-Backend {
    # 检查已有进程（通过 PID）
    if ($script:backend_pid) {
        $existing = Get-ProcessByPid $script:backend_pid
        if ($existing) {
            $lblStatus.Text = "后端已在运行 (PID: $script:backend_pid)"
            $script:backend_proc = $existing
            return $true
        }
        $script:backend_pid = $null
        $script:backend_proc = $null
    }

    # 确保虚拟环境存在
    if (-not (Test-Path (Join-Path $VENV_DIR "Scripts\python.exe"))) {
        if (-not (Test-Path $VENV_DIR)) {
            $lblStatus.Text = "正在创建虚拟环境..."
            Start-Sleep -Milliseconds 100
            & "python" -m venv $VENV_DIR
        }
        $lblStatus.Text = "正在安装依赖..."
        Start-Sleep -Milliseconds 100
        $pip = Join-Path $VENV_DIR "Scripts\pip.exe"
        & $pip install -q -r (Join-Path $BACKEND_DIR "requirements.txt")
    }

    # 依赖补装：以 requirements.txt 为准（含知识库技能依赖），已存在 venv 也会检查
    $lblStatus.Text = "正在检查 Python 依赖..."
    $python = Join-Path $VENV_DIR "Scripts\python.exe"
    $checkScript = Join-Path $PROJECT_DIR "scripts\check_deps.py"
    if (Test-Path $checkScript) {
        & $python $checkScript --quiet | Out-Null
    }

    $python = Join-Path $VENV_DIR "Scripts\python.exe"
    $backend_cmd = "-m uvicorn app.main:app --host $($cfg.BackendHost) --port $($cfg.BackendPort)"
    $lblStatus.Text = "正在启动后端 $($cfg.BackendHost):$($cfg.BackendPort)..."
    
    # 使用 UseShellExecute=$true 启动：避免 stdout/stderr 重定向导致缓冲区阻塞
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $python
    $psi.Arguments = $backend_cmd
    $psi.WorkingDirectory = $BACKEND_DIR
    $psi.UseShellExecute = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $script:backend_pid = $p.Id
    $script:backend_proc = $p
    Update-Status
    return $true
}

function Start-Frontend {
    # 检查已有进程（通过 PID）
    if ($script:frontend_pid) {
        $existing = Get-ProcessByPid $script:frontend_pid
        if ($existing) {
            $lblStatus.Text = "前端已在运行 (PID: $script:frontend_pid)"
            $script:frontend_proc = $existing
            return $true
        }
        $script:frontend_pid = $null
        $script:frontend_proc = $null
    }

    # 确保 node_modules 存在
    if (-not (Test-Path (Join-Path $FRONTEND_DIR "node_modules"))) {
        $lblStatus.Text = "正在安装前端依赖..."
        Start-Sleep -Milliseconds 100
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "npm"
        $psi.Arguments = "install --no-fund --no-audit"
        $psi.WorkingDirectory = $FRONTEND_DIR
        $psi.UseShellExecute = $true
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        $p.WaitForExit()
    }

    $lblStatus.Text = "正在启动前端 :$($cfg.FrontendPort)..."
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "cmd"
    $psi.Arguments = "/c npm run dev"
    $psi.WorkingDirectory = $FRONTEND_DIR
    $psi.UseShellExecute = $true
    $psi.CreateNoWindow = $true
    $env:PORT = $cfg.FrontendPort.ToString()
    $p = [System.Diagnostics.Process]::Start($psi)
    $script:frontend_pid = $p.Id
    $script:frontend_proc = $p
    Update-Status
    return $true
}

function Start-All {
    $btnStartAll.Enabled = $false
    $btnStartAll.Text = "启动中..."

    # 先同步配置
    Apply-Config

    # 启动后端
    Start-Backend
    
    # 启动前端
    Start-Frontend

    # 打开浏览器
    Start-Sleep -Seconds 3
    $url = "http://localhost:$($cfg.FrontendPort)"
    try { Start-Process $url } catch { }

    $btnStartAll.Enabled = $true
    $btnStartAll.Text = "启动全部"
    Update-Status
}

function Stop-All {
    $lblStatus.Text = "正在停止服务..."

    # 停止前端（通过 PID）
    if ($script:frontend_pid) {
        try {
            $p = Get-Process -Id $script:frontend_pid -ErrorAction Stop
            if (-not $p.HasExited) { $p.Kill(); $p.WaitForExit(5000) }
        } catch { }
    }
    $script:frontend_proc = $null
    $script:frontend_pid = $null

    # 停止后端（通过 PID）
    if ($script:backend_pid) {
        try {
            $p = Get-Process -Id $script:backend_pid -ErrorAction Stop
            if (-not $p.HasExited) { $p.Kill(); $p.WaitForExit(5000) }
        } catch { }
    }
    $script:backend_proc = $null
    $script:backend_pid = $null

    # 清理残留 python 后端进程（可能启动子进程）
    Get-Process -Name "python*" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "uvicorn" -or $_.CommandLine -match "app.main"
    } | ForEach-Object { try { $_.Kill() } catch { } }

    # 清理残留 node 前端进程
    Get-Process -Name "node*" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "vite" -or $_.CommandLine -match "frontend"
    } | ForEach-Object { try { $_.Kill() } catch { } }

    Update-Status
    $lblStatus.Text = "服务已停止"
}

function Restart-All {
    $btnRestart.Enabled = $false
    $btnRestart.Text = "重启中..."
    Stop-All
    Start-Sleep -Seconds 2
    Start-All
    $btnRestart.Enabled = $true
    $btnRestart.Text = "重启全部"
}

# ============================================================
# 配置应用
# ============================================================
function Apply-Config {
    # 验证输入
    $host_val = $txtBackendHost.Text.Trim()
    $port_val = $txtBackendPort.Text.Trim()
    $fport_val = $txtFrontendPort.Text.Trim()

    # 验证后端IP
    if ($host_val -eq "" -or $host_val -notmatch '^[\d\.]+$') {
        $lblConfigStatus.Text = "错误：后端监听IP格式无效"
        $lblConfigStatus.ForeColor = [System.Drawing.Color]::Red
        $txtBackendHost.BackColor = [System.Drawing.Color]::LightPink
        return $false
    }
    $txtBackendHost.BackColor = [System.Drawing.Color]::White

    # 验证后端端口
    if (-not ($port_val -match '^\d+$' -and [int]$port_val -ge 1 -and [int]$port_val -le 65535)) {
        $lblConfigStatus.Text = "错误：后端端口范围 1-65535"
        $lblConfigStatus.ForeColor = [System.Drawing.Color]::Red
        $txtBackendPort.BackColor = [System.Drawing.Color]::LightPink
        return $false
    }
    $txtBackendPort.BackColor = [System.Drawing.Color]::White

    # 验证前端端口
    if (-not ($fport_val -match '^\d+$' -and [int]$fport_val -ge 1 -and [int]$fport_val -le 65535)) {
        $lblConfigStatus.Text = "错误：前端端口范围 1-65535"
        $lblConfigStatus.ForeColor = [System.Drawing.Color]::Red
        $txtFrontendPort.BackColor = [System.Drawing.Color]::LightPink
        return $false
    }
    $txtFrontendPort.BackColor = [System.Drawing.Color]::White

    $cfg.BackendHost = $host_val
    $cfg.BackendPort = [int]$port_val
    $cfg.FrontendPort = [int]$fport_val

    Save-Config
    Sync-ViteConfig
    return $true
}

# ============================================================
# 状态监控
# ============================================================
function Update-Status {
    # 通过 PID 检查进程是否存活
    $b_run = $null -ne (Get-ProcessByPid $script:backend_pid)
    $f_run = $null -ne (Get-ProcessByPid $script:frontend_pid)

    # 检查端口是否响应（健康检查）
    $b_ok = $false
    if ($b_run) {
        try {
            $url = "http://$($cfg.BackendHost):$($cfg.BackendPort)/api/health"
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            $b_ok = $resp.StatusCode -eq 200
        } catch {
            # 健康检查失败时，尝试 TCP 端口检测（服务启动中但 HTTP 未就绪）
            try {
                $tcp = New-Object System.Net.Sockets.TcpClient
                $tcp.ConnectAsync($cfg.BackendHost, $cfg.BackendPort).Wait(2000) | Out-Null
                if ($tcp.Connected) { $b_ok = $true }
                $tcp.Close()
            } catch { }
        }
    }

    # 更新状态指示器
    if ($b_run -and $b_ok) {
        $lblStatus.Text = "后端: 运行中 $($cfg.BackendHost):$($cfg.BackendPort) (PID: $script:backend_pid)"
        $lblBackendStatus.Text = "● 运行中"
        $lblBackendStatus.ForeColor = [System.Drawing.Color]::Green
    } elseif ($b_run) {
        $lblStatus.Text = "后端: 启动中..."
        $lblBackendStatus.Text = "○ 启动中"
        $lblBackendStatus.ForeColor = [System.Drawing.Color]::Orange
    } else {
        $lblBackendStatus.Text = "○ 已停止"
        $lblBackendStatus.ForeColor = [System.Drawing.Color]::Gray
    }

    if ($f_run) {
        $lblFrontendStatus.Text = "● 运行中 (PID: $script:frontend_pid)"
        $lblFrontendStatus.ForeColor = [System.Drawing.Color]::Green
    } else {
        $lblFrontendStatus.Text = "○ 已停止"
        $lblFrontendStatus.ForeColor = [System.Drawing.Color]::Gray
    }

    # 更新后端URL链接
    $lblBackendUrl.Text = "http://$($cfg.BackendHost):$($cfg.BackendPort)/docs"
    $lblFrontendUrl.Text = "http://localhost:$($cfg.FrontendPort)"
}

# ============================================================
# 构建 GUI
# ============================================================
Load-Config

$form = New-Object System.Windows.Forms.Form
$form.Text = "深信服售后技术支持 Agent - 服务管理控制台"
$form.Size = New-Object System.Drawing.Size(540, 520)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon((Get-Command powershell).Source)

# ===== 标题 =====
$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = "深信服售后技术支持 Agent"
$lblTitle.Font = New-Object System.Drawing.Font("Microsoft YaHei", 14, [System.Drawing.FontStyle]::Bold)
$lblTitle.Size = New-Object System.Drawing.Size(500, 32)
$lblTitle.Location = New-Object System.Drawing.Point(20, 15)
$lblTitle.TextAlign = "MiddleCenter"
$form.Controls.Add($lblTitle)

$lblSubtitle = New-Object System.Windows.Forms.Label
$lblSubtitle.Text = "服务管理控制台"
$lblSubtitle.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9)
$lblSubtitle.Size = New-Object System.Drawing.Size(500, 18)
$lblSubtitle.Location = New-Object System.Drawing.Point(20, 48)
$lblSubtitle.TextAlign = "MiddleCenter"
$lblSubtitle.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($lblSubtitle)

# ===== 状态监控区域 =====
$grpStatus = New-Object System.Windows.Forms.GroupBox
$grpStatus.Text = "服务状态"
$grpStatus.Size = New-Object System.Drawing.Size(490, 100)
$grpStatus.Location = New-Object System.Drawing.Point(20, 75)
$form.Controls.Add($grpStatus)

# 后端状态
$lblBackendLabel = New-Object System.Windows.Forms.Label
$lblBackendLabel.Text = "后端 API:"
$lblBackendLabel.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9, [System.Drawing.FontStyle]::Bold)
$lblBackendLabel.Size = New-Object System.Drawing.Size(70, 22)
$lblBackendLabel.Location = New-Object System.Drawing.Point(15, 25)
$grpStatus.Controls.Add($lblBackendLabel)

$lblBackendStatus = New-Object System.Windows.Forms.Label
$lblBackendStatus.Text = "○ 已停止"
$lblBackendStatus.Size = New-Object System.Drawing.Size(120, 22)
$lblBackendStatus.Location = New-Object System.Drawing.Point(85, 25)
$lblBackendStatus.ForeColor = [System.Drawing.Color]::Gray
$grpStatus.Controls.Add($lblBackendStatus)

$lblBackendUrl = New-Object System.Windows.Forms.LinkLabel
$lblBackendUrl.Text = "http://127.0.0.1:8600/docs"
$lblBackendUrl.Size = New-Object System.Drawing.Size(270, 22)
$lblBackendUrl.Location = New-Object System.Drawing.Point(200, 25)
$lblBackendUrl.LinkColor = [System.Drawing.Color]::RoyalBlue
$lblBackendUrl.add_Click({ Start-Process $lblBackendUrl.Text })
$grpStatus.Controls.Add($lblBackendUrl)

# 前端状态
$lblFrontendLabel = New-Object System.Windows.Forms.Label
$lblFrontendLabel.Text = "前端界面:"
$lblFrontendLabel.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9, [System.Drawing.FontStyle]::Bold)
$lblFrontendLabel.Size = New-Object System.Drawing.Size(70, 22)
$lblFrontendLabel.Location = New-Object System.Drawing.Point(15, 55)
$grpStatus.Controls.Add($lblFrontendLabel)

$lblFrontendStatus = New-Object System.Windows.Forms.Label
$lblFrontendStatus.Text = "○ 已停止"
$lblFrontendStatus.Size = New-Object System.Drawing.Size(120, 22)
$lblFrontendStatus.Location = New-Object System.Drawing.Point(85, 55)
$lblFrontendStatus.ForeColor = [System.Drawing.Color]::Gray
$grpStatus.Controls.Add($lblFrontendStatus)

$lblFrontendUrl = New-Object System.Windows.Forms.LinkLabel
$lblFrontendUrl.Text = "http://localhost:5173"
$lblFrontendUrl.Size = New-Object System.Drawing.Size(270, 22)
$lblFrontendUrl.Location = New-Object System.Drawing.Point(200, 55)
$lblFrontendUrl.LinkColor = [System.Drawing.Color]::RoyalBlue
$lblFrontendUrl.add_Click({ Start-Process $lblFrontendUrl.Text })
$grpStatus.Controls.Add($lblFrontendUrl)

# ===== 操作按钮 =====
$grpActions = New-Object System.Windows.Forms.GroupBox
$grpActions.Text = "操作"
$grpActions.Size = New-Object System.Drawing.Size(490, 60)
$grpActions.Location = New-Object System.Drawing.Point(20, 185)
$form.Controls.Add($grpActions)

$btnStartAll = New-Object System.Windows.Forms.Button
$btnStartAll.Text = "启动全部"
$btnStartAll.Size = New-Object System.Drawing.Size(110, 30)
$btnStartAll.Location = New-Object System.Drawing.Point(15, 22)
$btnStartAll.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9, [System.Drawing.FontStyle]::Bold)
$btnStartAll.BackColor = [System.Drawing.Color]::ForestGreen
$btnStartAll.ForeColor = [System.Drawing.Color]::White
$btnStartAll.FlatStyle = "Flat"
$btnStartAll.add_Click({ Start-All })
$grpActions.Controls.Add($btnStartAll)

$btnStop = New-Object System.Windows.Forms.Button
$btnStop.Text = "停止全部"
$btnStop.Size = New-Object System.Drawing.Size(110, 30)
$btnStop.Location = New-Object System.Drawing.Point(135, 22)
$btnStop.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9, [System.Drawing.FontStyle]::Bold)
$btnStop.BackColor = [System.Drawing.Color]::Crimson
$btnStop.ForeColor = [System.Drawing.Color]::White
$btnStop.FlatStyle = "Flat"
$btnStop.add_Click({ Stop-All })
$grpActions.Controls.Add($btnStop)

$btnRestart = New-Object System.Windows.Forms.Button
$btnRestart.Text = "重启全部"
$btnRestart.Size = New-Object System.Drawing.Size(110, 30)
$btnRestart.Location = New-Object System.Drawing.Point(255, 22)
$btnRestart.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9, [System.Drawing.FontStyle]::Bold)
$btnRestart.BackColor = [System.Drawing.Color]::DarkOrange
$btnRestart.ForeColor = [System.Drawing.Color]::White
$btnRestart.FlatStyle = "Flat"
$btnRestart.add_Click({ Restart-All })
$grpActions.Controls.Add($btnRestart)

$btnOpenBrowser = New-Object System.Windows.Forms.Button
$btnOpenBrowser.Text = "打开前端"
$btnOpenBrowser.Size = New-Object System.Drawing.Size(110, 30)
$btnOpenBrowser.Location = New-Object System.Drawing.Point(370, 22)
$btnOpenBrowser.add_Click({ Start-Process "http://localhost:$($cfg.FrontendPort)" })
$grpActions.Controls.Add($btnOpenBrowser)

# ===== 配置区域 =====
$grpConfig = New-Object System.Windows.Forms.GroupBox
$grpConfig.Text = "监听配置"
$grpConfig.Size = New-Object System.Drawing.Size(490, 130)
$grpConfig.Location = New-Object System.Drawing.Point(20, 255)
$form.Controls.Add($grpConfig)

# 后端 IP
$lblBackendHost = New-Object System.Windows.Forms.Label
$lblBackendHost.Text = "后端监听 IP:"
$lblBackendHost.Size = New-Object System.Drawing.Size(90, 24)
$lblBackendHost.Location = New-Object System.Drawing.Point(15, 25)
$lblBackendHost.TextAlign = "MiddleLeft"
$grpConfig.Controls.Add($lblBackendHost)

$txtBackendHost = New-Object System.Windows.Forms.TextBox
$txtBackendHost.Text = $cfg.BackendHost
$txtBackendHost.Size = New-Object System.Drawing.Size(130, 24)
$txtBackendHost.Location = New-Object System.Drawing.Point(105, 25)
$grpConfig.Controls.Add($txtBackendHost)

# 后端端口
$lblBackendPort = New-Object System.Windows.Forms.Label
$lblBackendPort.Text = "后端端口:"
$lblBackendPort.Size = New-Object System.Drawing.Size(70, 24)
$lblBackendPort.Location = New-Object System.Drawing.Point(250, 25)
$lblBackendPort.TextAlign = "MiddleLeft"
$grpConfig.Controls.Add($lblBackendPort)

$txtBackendPort = New-Object System.Windows.Forms.TextBox
$txtBackendPort.Text = $cfg.BackendPort
$txtBackendPort.Size = New-Object System.Drawing.Size(80, 24)
$txtBackendPort.Location = New-Object System.Drawing.Point(320, 25)
$grpConfig.Controls.Add($txtBackendPort)

# 前端端口
$lblFrontendPort = New-Object System.Windows.Forms.Label
$lblFrontendPort.Text = "前端端口:"
$lblFrontendPort.Size = New-Object System.Drawing.Size(70, 24)
$lblFrontendPort.Location = New-Object System.Drawing.Point(15, 58)
$lblFrontendPort.TextAlign = "MiddleLeft"
$grpConfig.Controls.Add($lblFrontendPort)

$txtFrontendPort = New-Object System.Windows.Forms.TextBox
$txtFrontendPort.Text = $cfg.FrontendPort
$txtFrontendPort.Size = New-Object System.Drawing.Size(80, 24)
$txtFrontendPort.Location = New-Object System.Drawing.Point(85, 58)
$grpConfig.Controls.Add($txtFrontendPort)

# 应用按钮
$btnApply = New-Object System.Windows.Forms.Button
$btnApply.Text = "应用配置"
$btnApply.Size = New-Object System.Drawing.Size(100, 28)
$btnApply.Location = New-Object System.Drawing.Point(180, 58)
$btnApply.BackColor = [System.Drawing.Color]::SteelBlue
$btnApply.ForeColor = [System.Drawing.Color]::White
$btnApply.FlatStyle = "Flat"
$btnApply.add_Click({ Apply-Config })
$grpConfig.Controls.Add($btnApply)

# 恢复默认按钮
$btnRestore = New-Object System.Windows.Forms.Button
$btnRestore.Text = "恢复默认"
$btnRestore.Size = New-Object System.Drawing.Size(100, 28)
$btnRestore.Location = New-Object System.Drawing.Point(290, 58)
$btnRestore.add_Click({ Restore-DefaultConfig })
$grpConfig.Controls.Add($btnRestore)

# 配置状态提示
$lblConfigStatus = New-Object System.Windows.Forms.Label
$lblConfigStatus.Text = "修改配置后点击「应用配置」保存并同步到前端代理"
$lblConfigStatus.Size = New-Object System.Drawing.Size(470, 20)
$lblConfigStatus.Location = New-Object System.Drawing.Point(15, 95)
$lblConfigStatus.Font = New-Object System.Drawing.Font("Microsoft YaHei", 8)
$lblConfigStatus.ForeColor = [System.Drawing.Color]::Gray
$grpConfig.Controls.Add($lblConfigStatus)

# ===== 状态栏 =====
$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Text = "就绪"
$lblStatus.Size = New-Object System.Drawing.Size(500, 22)
$lblStatus.Location = New-Object System.Drawing.Point(20, 400)
$lblStatus.Font = New-Object System.Drawing.Font("Microsoft YaHei", 8)
$lblStatus.ForeColor = [System.Drawing.Color]::DimGray
$form.Controls.Add($lblStatus)

# ===== 定时器：每秒刷新状态 =====
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 3000
$timer.add_Tick({ Update-Status })
$timer.Start()

# ============================================================
# 启动
# ============================================================
$form.Add_Shown({ Update-Status })
$form.Add_FormClosed({
    $timer.Stop()
    # 询问是否停止服务（通过 PID 检查）
    $b = $null -ne (Get-ProcessByPid $script:backend_pid)
    $f = $null -ne (Get-ProcessByPid $script:frontend_pid)
    if ($b -or $f) {
        $result = [System.Windows.Forms.MessageBox]::Show(
            "服务正在运行，关闭前是否停止服务？", "确认退出",
            [System.Windows.Forms.MessageBoxButtons]::YesNoCancel,
            [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($result -eq "Yes") { Stop-All }
        elseif ($result -eq "Cancel") { $form.Close() }
    }
})
[System.Windows.Forms.Application]::Run($form)