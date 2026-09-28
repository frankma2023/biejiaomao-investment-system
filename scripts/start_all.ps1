# 投资系统一键启动：Flask API + Web 前门 + Pinggy 外网隧道
#
# 用法（在项目根目录）：
#     powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
# 或直接双击根目录的「启动投资网站.bat」。
#
# 特性：
#   - 幂等：已在运行的服务不会重复启动（按端口判断）
#   - 打印手机可用的三类地址：本机 / 家庭局域网 / 外网（Pinggy）
#   - 隧道断线自动重连；每次重连的新地址会重新打印、复制到剪贴板并写入 data\pinggy_url.txt
#   - 免费隧道 60 分钟到期，到期后脚本自动重连并打印新地址（手机需用新地址重开）
#   - 日志落在 data\logs\ 下，方便排查
#   - Ctrl+C 只停隧道；Flask 与前门保持运行（要全停请用「停止投资网站.bat」）
#
# 注意：本文件必须保存为「UTF-8 带 BOM」。Windows PowerShell 5.1 读取无 BOM 的
# .ps1 时会按 GBK 解码，中文注释会被拆坏并引发语法错误。改完请确认 BOM 仍在。

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
$PidFile = Join-Path $Root 'data\start_all.pid'
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

# 端口是幂等的，隧道和重连循环不是：重复双击会留下多条隧道，各自断线还会各自重连。
# 顺序不能反 —— 先结束上一次的启动脚本（否则它 5 秒后把被杀掉的 ssh 再拉起来），
# 再清掉映射本端口的 ssh。
#
# 上一次的进程靠 data\start_all.pid 精确定位，不靠「命令行里含 start_all.ps1」这种模糊匹配：
# 那个写法会误杀任何命令行里恰好提到本文件名的 PowerShell（包括调用者的父进程）。
$oldPid = 0
if ((Test-Path $PidFile) -and [int]::TryParse((Get-Content $PidFile -Raw -ErrorAction SilentlyContinue).Trim(), [ref]$oldPid)) {
    $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($oldProc -and $oldPid -ne $PID -and $oldProc.ProcessName -match '^(powershell|pwsh)$') {
        $oldCmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue).CommandLine
        if ($oldCmd -like '*start_all.ps1*') {
            Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
            Write-Host "  已结束上一次的启动脚本（pid $oldPid）。" -ForegroundColor DarkGray
            Start-Sleep -Milliseconds 800
        }
    }
}
Set-Content -Path $PidFile -Value $PID -Encoding ASCII

$stale = @(Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*127.0.0.1:$WebPort*" -and $_.ProcessId -ne $PID })
if ($stale.Count) {
    $stale | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "  已清理 $($stale.Count) 条本端口的旧隧道。" -ForegroundColor DarkGray
}

# -T（不分配伪终端）必须保留：Pinggy 在有 tty 时输出整屏 ANSI 界面且不含换行，
# PowerShell 管道永远读不到完整行，就拿不到公网地址。无 tty 时输出是纯文本行。
$sshArgs = @(
    '-T',
    '-p', '443',
    '-R', "0:127.0.0.1:$WebPort",
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-o', 'ExitOnForwardFailure=yes',
    'free.pinggy.io'
)

# ssh 用 Start-Process 跑并把两个流落盘，不接管道：
# ssh 会往 stderr 写「Allocated port N ...」，脚本顶部 $ErrorActionPreference='Stop'
# 会把该行当成终止错误抛出，pipeline 一崩 ssh 立刻被回收，隧道永远建不起来。
$sshExe = (Get-Command 'ssh.exe' -ErrorAction SilentlyContinue).Source
if (-not $sshExe) { $sshExe = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe' }
if (-not (Test-Path $sshExe)) {
    Write-Warn2 '找不到 ssh.exe，无法开外网隧道。'
    Write-Host '  Windows「设置 → 系统 → 可选功能」里添加「OpenSSH 客户端」后重试。' -ForegroundColor Yellow
    Write-Host '  本机与局域网地址不受影响，现在就可使用。' -ForegroundColor Yellow
    exit 1
}

$TunnelOut = Join-Path $LogDir 'tunnel.out.log'
$TunnelErr = Join-Path $LogDir 'tunnel.err.log'

# 从一行输出里认出公网地址。输出同时含 free.pinggy.net（主地址）、
# run.pinggy-free.link（备用）和 dashboard.pinggy.io（提示语里的官网，必须排除）。
function Show-TunnelUrl([string]$line) {
    if ($script:gotUrl) { return }
    $m = [regex]::Match($line, 'https://(?!dashboard\.)[A-Za-z0-9-]+\.free\.pinggy\.net')
    if (-not $m.Success) {
        $m = [regex]::Match($line, 'https://(?!dashboard\.)[A-Za-z0-9-]+\.(?:pinggy-free\.link|pinggy\.net|pinggy\.io)')
    }
    if (-not $m.Success) { return }

    $script:gotUrl = $true
    $script:tunnelUrl = $m.Value
    Set-Content -Path $UrlFile -Value $script:tunnelUrl -Encoding UTF8
    $copied = $false
    try { Set-Clipboard -Value $script:tunnelUrl; $copied = $true } catch { $copied = $false }

    Write-Host ''
    Write-Host ('  ' + ('=' * 58)) -ForegroundColor Green
    Write-Host '   手机用流量打开这个地址：' -ForegroundColor Green
    Write-Host ''
    Write-Host "     $script:tunnelUrl" -ForegroundColor Black -BackgroundColor Green
    Write-Host ''
    if ($copied) { Write-Host '   （地址已复制到剪贴板，也写入 data\pinggy_url.txt）' -ForegroundColor Green }
    else         { Write-Host '   （地址已写入 data\pinggy_url.txt）' -ForegroundColor Green }
    Write-Host '   免费隧道 60 分钟到期，到时这里会自动打印新地址。' -ForegroundColor Yellow
    Write-Host '   手机上第一次打开会有一个英文提示页，点一下继续就进站。' -ForegroundColor Yellow

    # 自检：带 X-Pinggy-No-Screen 头穿透免费版的中间提示页，确认外网真能拿到内容。
    # 不做这一步的话，页面打不开时无法区分是隧道没通还是反代没通。
    try {
        $probe = Invoke-WebRequest ($script:tunnelUrl + '/cockpit/') -UseBasicParsing -TimeoutSec 25 `
            -Headers @{ 'X-Pinggy-No-Screen' = '1' }
        if ($probe.StatusCode -eq 200) {
            Write-Host "   [OK] 外网自检通过（/cockpit/ 返回 200，$([int]$probe.RawContentLength) 字节）" -ForegroundColor Green
        } else {
            Write-Host "   [!] 外网自检返回 $($probe.StatusCode)，隧道已通但前门可能没起来" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "   [!] 外网自检失败：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host '     隧道地址本身有效，可换手机流量再试；持续失败请看重连提示。' -ForegroundColor Yellow
    }
    Write-Host ('  ' + ('=' * 58)) -ForegroundColor Green
    Write-Host ''
}

$attempt = 0
while ($true) {
    $attempt++
    if ($attempt -gt 1) {
        Write-Host ''
        Write-Warn2 "隧道第 $attempt 次尝试 ..."
    }

    Remove-Item $TunnelOut, $TunnelErr -ErrorAction SilentlyContinue
    $script:gotUrl = $false
    $ssh = Start-Process -FilePath $sshExe -ArgumentList $sshArgs `
        -RedirectStandardOutput $TunnelOut -RedirectStandardError $TunnelErr `
        -PassThru -WindowStyle Hidden

    $nOut = 0
    $nErr = 0
    while (-not $ssh.HasExited) {
        Start-Sleep -Seconds 1
        foreach ($line in @(Get-Content $TunnelOut -Encoding UTF8 -ErrorAction SilentlyContinue | Select-Object -Skip $nOut)) {
            $nOut++
            # ssh 每几秒刷一行 "RB: .. SB: .. TC: .. AC: .." 的流量计数，对使用者是噪音。
            if ($line -notmatch '^\s*RB:\s') {
                Write-Host "  $line" -ForegroundColor DarkGray
                Show-TunnelUrl $line
            }
        }
        foreach ($line in @(Get-Content $TunnelErr -Encoding UTF8 -ErrorAction SilentlyContinue | Select-Object -Skip $nErr)) {
            $nErr++
            Write-Host "  $line" -ForegroundColor DarkGray
            Show-TunnelUrl $line
        }
    }
    $ssh.Dispose()
    Write-Host ''
    Write-Warn2 '隧道已断开，5 秒后重连（地址会变，注意看新的那行）。Ctrl+C 可退出。'
    Start-Sleep -Seconds 5
}
