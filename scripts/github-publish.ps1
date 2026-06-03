# Create the GitHub repo (if needed) and push main.
# Prerequisite: gh auth login -h github.com -p https -w
#
# Usage:
#   .\scripts\github-publish.ps1
#   .\scripts\github-publish.ps1 -Org otrm

param(
    [string]$Org = "",
    [string]$RepoName = "meg"
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "Install GitHub CLI: winget install GitHub.cli"
}

gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not logged in. Run: gh auth login -h github.com -p https -w"
    exit 1
}

$fullName = if ($Org) { "$Org/$RepoName" } else { $RepoName }
$desc = "AI-powered FFmpeg assistant for the terminal"

if (git remote get-url origin 2>$null) {
    git push -u origin main
    Write-Host "Pushed to origin (main)."
    exit 0
}

if ($Org) {
    gh repo create $fullName --public --source=. --remote=origin --push --description $desc
} else {
    gh repo create $RepoName --public --source=. --remote=origin --push --description $desc
}

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$login = gh api user -q .login
$url = if ($Org) { "https://github.com/$fullName" } else { "https://github.com/$login/$RepoName" }
Write-Host "Repository ready: $url"
