$root = "C:\Dev\nick-cosmetics-python-platform"
$launcher = "C:\Dev\nick-cosmetics-python-platform\_start_storefront_trial.ps1"
$pub = "https://laptop-ocn4otih.tail191ff5.ts.net"
function Say($m) { Write-Host ("[start_trial] " + $m) }
function Code($u) { try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 -Uri $u).StatusCode } catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return 0 } } }
function WaitFor($u, $tries) { for ($i = 0; $i -lt $tries; $i++) { if ((Code $u) -eq 200) { return $true }; Start-Sleep -Seconds 5 }; return $false }
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  if (-not (Test-Path $dd)) { Say "Docker Desktop not found at its default path. Start it by hand, then run this again."; Read-Host "Press Enter to close"; exit 1 }
  Say "Starting Docker Desktop, please wait..."
  Start-Process $dd
  $up = $false
  for ($i = 0; $i -lt 36; $i++) { Start-Sleep -Seconds 5; docker info 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $up = $true; break } }
  if (-not $up) { Say "Docker was not ready after 3 minutes. Open Docker Desktop, wait until it says running, then run this again."; Read-Host "Press Enter to close"; exit 1 }
}
Say "Docker is running."
Say "Starting containers (API, Postgres, Redis)..."
Push-Location (Join-Path $root "infra\docker")
docker compose -f docker-compose.dev.yml up -d
Pop-Location
if (WaitFor "http://127.0.0.1:8000/docs" 24) { Say "API is up (port 8000)." } else { Say "API did not answer in 2 minutes. Run: docker logs --tail 15 nickcosmetics-api" }
$listening = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($listening) { Say "Storefront already running on port 8001, leaving it alone." } else {
  Say "Starting storefront in its own window. Keep that window open."
  Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-File",$launcher
}
if (WaitFor "http://127.0.0.1:8001/shop" 12) { Say "Storefront is up (port 8001)." } else { Say "Storefront did not answer in 1 minute. Look at the storefront window for errors." }
$adminBat = Join-Path $root "run_web_admin.bat"
$adminListening = Get-NetTCPConnection -LocalPort 5500 -State Listen -ErrorAction SilentlyContinue
if ($adminListening) { Say "Admin site already running on port 5500, leaving it alone." } elseif (-not (Test-Path $adminBat)) { Say "run_web_admin.bat not found, admin site not started." } else {
  Say "Starting admin site in its own window. Keep that window open."
  Start-Process $adminBat -WorkingDirectory $root
}
if (WaitFor "http://127.0.0.1:5500/js/api.js" 6) { Say "Admin site is up (port 5500). On this laptop open http://127.0.0.1:5500" } else { Say "Admin site did not answer in 30 seconds. Look at the admin window for errors." }
if (Get-Command tailscale -ErrorAction SilentlyContinue) { Say "Tailscale status:"; tailscale status | Select-Object -First 3; Say "Funnel status:"; tailscale funnel status } else { Say "tailscale command not found. Open the Tailscale app and make sure it is connected." }
$p = Code ($pub + "/shop")
if ($p -eq 200) { Say "PUBLIC CHECK OK: shop answered 200 on the public address." } else { Say "PUBLIC CHECK GOT $p (can be a false alarm from the laptop itself). Test on your phone with mobile data." }
Say "Send friends ONLY this link: $pub"
Say "Keep this window and the storefront window open. Do not shut the laptop or let it sleep."
Read-Host "Press Enter to close this window (the site keeps running)"
