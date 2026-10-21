@echo off
cd /d "%~dp0apps\web_admin"
"%~dp0.venv\Scripts\python.exe" -m http.server 5500 --bind 0.0.0.0
