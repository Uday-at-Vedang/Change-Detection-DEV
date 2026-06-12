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
git push hf-dev master:main
Write-Host "Done. Dev app: https://huggingface.co/spaces/coderuday21/satdetect-dev"
