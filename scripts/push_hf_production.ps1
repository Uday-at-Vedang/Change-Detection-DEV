# Push master (no login) to the live Hugging Face Space (satdetect).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (git remote get-url hf 2>$null)) {
    Write-Error "Remote 'hf' not found. Add it with:`n  git remote add hf https://huggingface.co/spaces/coderuday21/satdetect"
}

# Keep production branch aligned with master
git branch -f production master

Write-Host "Pushing master -> hf/main (satdetect, no login)..."
git push hf master:main
Write-Host "Done. Live app: https://huggingface.co/spaces/coderuday21/satdetect"
