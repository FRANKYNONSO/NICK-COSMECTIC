@echo off
cd /d "%~dp0apps\web_admin"
rem fix_live05: listen on this PC only; the tunnel reaches it through 127.0.0.1.
"%~dp0.venv\Scripts\python.exe" -m http.server 5500 --bind 127.0.0.1
