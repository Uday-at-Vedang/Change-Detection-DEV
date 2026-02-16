@echo off
cd /d "%~dp0"
echo Starting Satellite Change Detection...
echo.
echo Open in your browser:  http://localhost:8000
echo.
start http://localhost:8000
uvicorn app.main:app --host 127.0.0.1 --port 8000
