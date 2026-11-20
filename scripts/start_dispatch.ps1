# Dispatch (SupoClip) One-Click Desktop Launcher
# Starts Docker engine, containers, network tunnel, and opens browser.

$Host.UI.RawUI.WindowTitle = "Dispatch - Video AI Studio"

Clear-Host
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "                 DISPATCH VIDEO AI LAUNCHER                 " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Ensure Docker Engine is active
Write-Host "`n[1/4] Checking Docker engine status..." -ForegroundColor Yellow
$dockerReady = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true }
} catch {}

if (-not $dockerReady) {
    Write-Host "Docker is not running. Starting Docker Desktop engine..." -ForegroundColor Yellow
    
    $dockerBackend = "C:\Program Files\Docker\Docker\resources\com.docker.backend.exe"
    $dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    
    if (Test-Path $dockerBackend) {
        Start-Process -FilePath $dockerBackend -WindowStyle Hidden
    } elseif (Test-Path $dockerDesktop) {
        Start-Process -FilePath $dockerDesktop
    }
    
    $timeout = 60
    $waited = 0
    while (-not $dockerReady -and $waited -lt $timeout) {
        Start-Sleep -Seconds 2
        $waited += 2
        Write-Host "Waiting for Docker engine to become ready ($waited s)..." -ForegroundColor Gray
        try {
            $null = docker info 2>&1
            if ($LASTEXITCODE -eq 0) { $dockerReady = $true }
        } catch {}
    }
}

if (-not $dockerReady) {
    Write-Host "`n[!] ERROR: Could not connect to Docker." -ForegroundColor Red
    Write-Host "Please start Docker Desktop manually and run this again." -ForegroundColor Red
    pause
    exit 1
}
Write-Host "✓ Docker engine is online!" -ForegroundColor Green

# 2. Ensure SupoClip Containers are running
Write-Host "`n[2/4] Starting Dispatch containers (PostgreSQL, Redis, Backend, Worker, Frontend)..." -ForegroundColor Yellow
Set-Location "C:\CODE\supoclip"
docker compose up -d

# 3. Ensure Cloudflare Tunnel is running
Write-Host "`n[3/4] Checking network tunnels..." -ForegroundColor Yellow
$cfPath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$logPath = "C:\CODE\supoclip\scripts\cloudflared.log"

if (Test-Path $cfPath) {
    $cfRunning = Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue
    if (-not $cfRunning) {
        Write-Host "Starting Cloudflare tunnel in background..." -ForegroundColor Gray
        Start-Process -FilePath $cfPath -ArgumentList "tunnel --url http://localhost:3107 --logfile `"$logPath`"" -WindowStyle Hidden
        
        # Wait up to 10s to extract the assigned tunnel URL
        $cfUrl = $null
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Seconds 1
            if (Test-Path $logPath) {
                $match = Select-String -Path $logPath -Pattern "https://[a-zA-Z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1
                if ($match) {
                    $cfUrl = $match.Matches[0].Value
                    break
                }
            }
        }
        
        if ($cfUrl) {
            Write-Host "✓ Cloudflare tunnel connected: $cfUrl" -ForegroundColor Green
            # Update Netlify proxy in background
            Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -Command `"cd 'C:\CODE\supoclip\deploy\netlify-proxy'; npx --yes netlify env:set UPSTREAM_ORIGIN $cfUrl`"" -WindowStyle Hidden
        }
    } else {
        Write-Host "✓ Cloudflare tunnel is active." -ForegroundColor Green
    }
}

# Ensure Supervisor daemon is active
$supRunning = Get-CimInstance Win32_Process -Filter "CommandLine like '%tunnel_supervisor.py%'"
if (-not $supRunning) {
    Start-Process "C:\Users\rohit\AppData\Local\Programs\Python\Python310\pythonw.exe" -ArgumentList "C:\CODE\supoclip\scripts\tunnel_supervisor.py" -WorkingDirectory "C:\CODE\supoclip" -WindowStyle Hidden
}

# 4. Wait for web app health check
Write-Host "`n[4/4] Verifying Dispatch web service..." -ForegroundColor Yellow
$appReady = $false
$retries = 20
while (-not $appReady -and $retries -gt 0) {
    $code = & curl.exe -s -o NUL -w "%{http_code}" http://localhost:3107
    if ($code -eq "200" -or $code -eq "307" -or $code -eq "308") {
        $appReady = $true
        break
    }
    Start-Sleep -Seconds 1
    $retries--
    Write-Host "." -NoNewline -ForegroundColor Gray
}

Write-Host ""
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "          DISPATCH IS LIVE AND READY FOR ACTION!            " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Local:  http://localhost:3107" -ForegroundColor Cyan
Write-Host "Public: https://dispatch.theresolvia.com" -ForegroundColor Cyan
Write-Host ""
Write-Host "Opening Dispatch in your browser..." -ForegroundColor White

$targetUrl = "https://dispatch.theresolvia.com"
$pubCode = & curl.exe -s -o NUL -w "%{http_code}" https://dispatch.theresolvia.com
if ($pubCode -ne "200") {
    $targetUrl = "http://localhost:3107"
}

Start-Process $targetUrl
Start-Sleep -Seconds 3
