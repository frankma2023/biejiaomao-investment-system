# 投资系统一键启动：Flask API + Web 前门 + Pinggy 外网隧道
#
# 用法（在项目根目录）：
#     powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
# 或直接双击根目录的「启动投资网站.bat」。
#
# 特性：
#   - 幂等：已在运行的服务不会重复启动（按端口判断）
#   - 打印手机可用的三类地址：本机 / 家庭局域网 / 外网（Pinggy）
#   - 隧道断线自动重连；每次重连的新地址会重新打印并写入 data\pinggy_url.txt
#   - 日志落在 data\logs\ 下，方便排查
#   - Ctrl+C 只停隧道；Flask 与前门保持运行（要全停请用「停止投资网站.bat」）

[CmdletBinding()]
param(
    [int]$ApiPort = 8788,          # Flask API
    [int]$WebPort = 8772,          # Web 前门（静态 + /api 反代）
    [switch]$NoTunnel,             # 只要局域网，不开外网隧道
    [switch]$Local                 # 同 NoTunnel（语义化别名）
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot          # scripts\ -> 项目根
Set-Location $Root

$LogDir = Join-Path $Root 'data\logs'
$UrlFile = Join-Path $Root 'data\pinggy_url.txt'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Head($text) {
    Write-Host ''
    Write-Host ('─' * 62) -ForegroundColor DarkGray
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('─' * 62) -ForegroundColor DarkGray
}
function Write-Ok($text)   { Write-Host "  [OK]   $text" -ForegroundColor Green }
function Write-Warn2($text){ Write-Host "  [警告] $text" -ForegroundColor Yellow }
function Write-Err2($text) { Write-Host "  [失败] $text" -ForegroundColor Red }

function Test-Port([int]$Port) {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Port([int]$Port, [int]$TimeoutSec = 40) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-Python {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
        'C:\Program Files\Python312\python.exe',
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")) {
        if (Test-Path $p) { return $p }
    }
    throw '找不到 python，请确认已安装并加入 PATH'
}

# ── 1. Flask API ───────────────────────────────────────────
Write-Head '投资系统启动'
$Python = Get-Python
Write-Host "  python : $Python"

if (Test-Port $ApiPort) {
    Write-Ok "Flask API 已在 :$ApiPort 运行，跳过"
} else {
    Write-Host "  启动 Flask API (:$ApiPort) ..."
    Start-Process -FilePath $Python -ArgumentList 'src\server.py' `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'api.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'api.err.log') | Out-Null
    if (Wait-Port $ApiPort) { Write-Ok "Flask API 就绪 (:$ApiPort)" }
    else {
        Write-Err2 "Flask API 未能在 :$ApiPort 启动，看看 data\logs\api.err.log"
        exit 1
    }
}

# ── 2. Web 前门（静态 + /api 反代）─────────────────────────
if (Test-Port $WebPort) {
    Write-Ok "Web 前门已在 :$WebPort 运行，跳过"
} else {
    Write-Host "  启动 Web 前门 (:$WebPort) ..."
    Start-Process -FilePath $Python -ArgumentList 'scripts\serve_dev.py' `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'web.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'web.err.log') | Out-Null
    if (Wait-Port $WebPort) { Write-Ok "Web 前门就绪 (:$WebPort)" }
    else {
        Write-Err2 "Web 前门未能在 :$WebPort 启动，看看 data\logs\web.err.log"
        exit 1
    }
}

# 前门必须能代理 /api —— 这是手机/隧道访问的前提，启动后立刻验一次
try {
    $probe = Invoke-WebRequest "http://127.0.0.1:$WebPort/api/cockpit/cup-handle" `
        -UseBasicParsing -TimeoutSec 10
    Write-Ok "前门反代 /api 正常（$($probe.StatusCode)）"
} catch {
    Write-Warn2 "前门反代 /api 探测失败：$($_.Exception.Message)"
    Write-Warn2 "若 :$WebPort 上跑的不是 scripts/serve_dev.py，请先停掉它再重跑本脚本"
}

# ── 3. 可用地址 ───────────────────────────────────────────
Write-Head '可访问地址'
Write-Host "  本机    http://localhost:$WebPort/"

$lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
    Select-Object -First 1 -ExpandProperty IPAddress
if ($lan) {
    Write-Host "  家里WiFi http://${lan}:$WebPort/          （手机连同一个 WiFi 时用）"
} else {
    Write-Warn2 '未识别到局域网 IPv4 地址'
}

$v6 = Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -match '^2[0-9a-f]{3}:' -and $_.SuffixOrigin -ne 'Random' } |
    Select-Object -First 1 -ExpandProperty IPAddress
if ($v6) {
    Write-Host "  外网IPv6 http://[$v6]:$WebPort/" -ForegroundColor DarkGray
    Write-Host "           （需要光猫放行 IPv6 入站；放行不了就用下面的隧道）" -ForegroundColor DarkGray
}

if ($NoTunnel -or $Local) {
    Write-Head '完成（未开隧道）'
    Write-Host '  服务已在后台运行。要停止请双击「停止投资网站.bat」。'
    exit 0
}

# ── 4. Pinggy 隧道（断线自动重连）──────────────────────────
Write-Head 'Pinggy 外网隧道'
Write-Host '  正在建立隧道；下面出现 https:// 开头的地址后，'
Write-Host '  手机用流量直接打开它即可。按 Ctrl+C 只停隧道。' -ForegroundColor Yellow
Write-Host ''

$sshArgs = @(
    '-p', '443',
    '-R', "0:127.0.0.1:$WebPort",
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-o', 'ExitOnForwardFailure=yes',
    'free.pinggy.io'
)

$attempt = 0
while ($true) {
    $attempt++
    if ($attempt -gt 1) {
        Write-Host ''
        Write-Warn2 "隧道第 $attempt 次尝试 ..."
    }
    $script:gotUrl = $false
    try {
        & ssh @sshArgs 2>&1 | ForEach-Object {
            $line = [string]$_
            Write-Host "  $line" -ForegroundColor DarkGray
            if (-not $script:gotUrl -and $line -match 'https://[A-Za-z0-9._-]*pinggy[A-Za-z0-9._-]*') {
                $script:gotUrl = $true
                $url = $Matches[0]
                Set-Content -Path $UrlFile -Value $url -Encoding UTF8
                Write-Host ''
                Write-Host ('  ' + ('=' * 58)) -ForegroundColor Green
                Write-Host '   手机用流量打开这个地址：' -ForegroundColor Green
                Write-Host ''
                Write-Host "     $url" -ForegroundColor Black -BackgroundColor Green
                Write-Host ''
                Write-Host '   （已写入 data\pinggy_url.txt）' -ForegroundColor Green
                Write-Host ('  ' + ('=' * 58)) -ForegroundColor Green
                Write-Host ''
            }
        }
    } catch {
        Write-Warn2 "ssh 退出：$($_.Exception.Message)"
    }
    Write-Host ''
    Write-Warn2 '隧道已断开，5 秒后重连（地址会变，注意看新的那行）。Ctrl+C 可退出。'
    Start-Sleep -Seconds 5
}
