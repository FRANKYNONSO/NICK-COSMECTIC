$root = "C:\Dev\nick-cosmetics-python-platform"
Set-Location "$root\apps\ecommerce"
$pwd8 = [Environment]::GetEnvironmentVariable("NICK_STOREFRONT_PASSWORD", "User")
$sec8 = [Environment]::GetEnvironmentVariable("NICK_ECOMMERCE_SESSION_SECRET", "User")
if ($null -eq $pwd8 -or $pwd8.Length -ne 40) { "STOP: storefront password length is not 40. Nothing started."; return }
if ($null -eq $sec8 -or $sec8.Length -ne 64) { "STOP: session secret length is not 64. Nothing started."; return }
$busy = @(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)
if ($busy.Count -ne 0) { "STOP: port 8001 already has a listener. Nothing started."; return }
$env:NICK_STOREFRONT_PASSWORD = $pwd8
$env:NICK_ECOMMERCE_SESSION_SECRET = $sec8
"Starting storefront on port 8001. Close this window to stop it."
venv\Scripts\python.exe -m uvicorn main:app --port 8001