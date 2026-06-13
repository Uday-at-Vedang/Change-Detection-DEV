# Push master to the dev Hugging Face Space (satdetect-dev).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (git remote get-url hf-dev 2>$null)) {
    Write-Host "Adding remote hf-dev..."
    git remote add hf-dev https://huggingface.co/spaces/coderuday21/satdetect-dev
}

$branch = git rev-parse --abbrev-ref HEAD
if ($branch -ne "master") {
    Write-Host "Switching to master branch..."
    git checkout master
}

Write-Host "Pushing master -> hf-dev/main (satdetect-dev)..."
$force = $args -contains "-Force" -or $args -contains "--force"
if ($force) {
    git push hf-dev master:main --force
} else {
    git push hf-dev master:main
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Push rejected? Hugging Face may have a starter README commit."
        Write-Host "Re-run with: .\scripts\push_hf_dev.ps1 -Force"
        exit $LASTEXITCODE
    }
}
Write-Host "Done. Dev app: https://huggingface.co/spaces/coderuday21/satdetect-dev"
