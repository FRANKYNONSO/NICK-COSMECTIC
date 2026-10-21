@echo off
cd /d "%~dp0apps\api"
"%~dp0.venv\Scripts\python.exe" -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
