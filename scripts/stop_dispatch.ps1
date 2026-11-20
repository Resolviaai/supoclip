# Dispatch (SupoClip) One-Click Desktop Stopper
# Cleanly stops all Docker containers and network tunnels to free up system resources.

$Host.UI.RawUI.WindowTitle = "Dispatch - Stopping Video AI Engine"

Clear-Host
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "                    STOPPING DISPATCH                       " -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta

# 1. Stop Docker Containers
Write-Host "`n[1/2] Stopping Dispatch containers (Postgres, Redis, Backend, Worker, Frontend)..." -ForegroundColor Yellow
Set-Location "C:\CODE\supoclip"
docker compose stop

# 2. Stop Background Tunnels
Write-Host "`n[2/2] Stopping network tunnels..." -ForegroundColor Yellow
$stoppedAny = $false
$cf = Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue
if ($cf) {
    Stop-Process -Name "cloudflared" -Force -ErrorAction SilentlyContinue
    Write-Host "✓ Stopped Cloudflare tunnel." -ForegroundColor Green
    $stoppedAny = $true
}

$ng = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue
if ($ng) {
    Stop-Process -Name "ngrok" -Force -ErrorAction SilentlyContinue
    Write-Host "✓ Stopped ngrok tunnel." -ForegroundColor Green
    $stoppedAny = $true
}

if (-not $stoppedAny) {
    Write-Host "✓ No active tunnels were running." -ForegroundColor Gray
}

Write-Host ""
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  DISPATCH SHUT DOWN - RAM & CPU FREED UP SUCCESSFULLY!     " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Start-Sleep -Seconds 3
