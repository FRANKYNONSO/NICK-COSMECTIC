$root = $PSScriptRoot
Set-Location "$root\apps\ecommerce"
$pw = [Environment]::GetEnvironmentVariable("NICK_TRIAL_STOREFRONT_PASSWORD", "User")
$sec = [Environment]::GetEnvironmentVariable("NICK_TRIAL_ECOMMERCE_SESSION_SECRET", "User")
if ($null -eq $pw -or $pw.Length -ne 40) { "STOP: trial password missing. Run fix_live12.ps1 first."; return }
if ($null -eq $sec -or $sec.Length -ne 64) { "STOP: trial session secret missing. Run fix_live12.ps1 first."; return }
$busy = @(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)
if ($busy.Count -ne 0) { "STOP: port 8001 already has a listener. Nothing started."; return }
$env:NICK_STOREFRONT_PASSWORD = $pw
$env:NICK_ECOMMERCE_SESSION_SECRET = $sec
$env:NICK_STOREFRONT_EMAIL = "storefront@nickcosmetics.com"
$env:NICK_STOREFRONT_BRANCH_ID = "aac794fa-026c-463b-81e4-f7eff6cfb04c"
$env:NICK_API_BASE_URL = "http://127.0.0.1:8000"
$env:NICK_HTTPS_ONLY = "1"
"Starting TRIAL storefront on 127.0.0.1:8001. Close this window to stop it."
venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
