# Run from change_detection_webapp folder
Set-Location $PSScriptRoot
Write-Host "Starting Satellite Change Detection..." -ForegroundColor Cyan
Write-Host ""
Write-Host "Open in your browser:  http://localhost:8000" -ForegroundColor Green
Write-Host ""
Start-Process "http://localhost:8000"
uvicorn app.main:app --host 127.0.0.1 --port 8000
