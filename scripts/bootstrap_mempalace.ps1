$ErrorActionPreference = "Stop"

if (-not (Get-Command mempalace -ErrorAction SilentlyContinue)) {
  Write-Host "Installing mempalace..."
  pip install mempalace
}

if (-not (Test-Path "data/mempalace-palace")) {
  New-Item -ItemType Directory -Path "data/mempalace-palace" | Out-Null
}

Write-Host "MemPalace bootstrap complete."
