# 停止投资系统：结束占用 API 端口 / Web 前门端口的进程
#
# 用法：双击根目录的「停止投资网站.bat」，或
#     powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1

[CmdletBinding()]
param(
    [int]$ApiPort = 8788,
    [int]$WebPort = 8772
)

$ErrorActionPreference = 'Continue'

function Stop-Port([int]$Port, [string]$Name) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "  [跳过] $Name (:$Port) 未在运行" -ForegroundColor DarkGray
        return
    }
    foreach ($target in ($conns.OwningProcess | Sort-Object -Unique)) {
        $proc = Get-Process -Id $target -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        try {
            Stop-Process -Id $target -Force -ErrorAction Stop
            Write-Host "  [已停] $Name (:$Port)  pid=$target  $($proc.ProcessName)" -ForegroundColor Green
        } catch {
            Write-Host "  [失败] $Name (:$Port)  pid=$target  $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

Write-Host ''
Write-Host ('-' * 62) -ForegroundColor DarkGray
Write-Host '  停止投资系统' -ForegroundColor Cyan
Write-Host ('-' * 62) -ForegroundColor DarkGray

Stop-Port $WebPort 'Web 前门'
Stop-Port $ApiPort 'Flask API'

# 隧道是 ssh.exe，按命令行特征精确匹配，避免误杀其它 ssh（例如 DSH Desktop 自己的隧道）
$tunnels = Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*pinggy*' -and $_.CommandLine -like "*127.0.0.1:$WebPort*" }
if ($tunnels) {
    foreach ($t in $tunnels) {
        try {
            Stop-Process -Id $t.ProcessId -Force -ErrorAction Stop
            Write-Host "  [已停] Pinggy 隧道  pid=$($t.ProcessId)" -ForegroundColor Green
        } catch {
            Write-Host "  [失败] Pinggy 隧道  pid=$($t.ProcessId)" -ForegroundColor Red
        }
    }
} else {
    Write-Host '  [跳过] Pinggy 隧道 未在运行' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  完成。' -ForegroundColor Cyan
Write-Host ''
