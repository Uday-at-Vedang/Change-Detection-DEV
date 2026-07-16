# Daily sync: merge Priyanka's branch into uday before starting work.
# Run from change_detection_webapp:
#   .\scripts\sync_uday_from_priyanka.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$remote = "github-dev"
$base = "New/Priyanka"
$branch = "uday"

Write-Host "Fetching $remote..."
git fetch $remote

$current = git branch --show-current
if ($current -ne $branch) {
    Write-Host "Checking out $branch..."
    git checkout $branch
}

Write-Host "Merging $remote/$base into $branch..."
git merge "$remote/$base" -m "sync: merge $base into $branch"

Write-Host "Done. Branch $branch is up to date with $base."
