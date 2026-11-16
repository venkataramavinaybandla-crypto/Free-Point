<#
.SYNOPSIS
    Starts the Free Point local development stack for demo without invite codes.
.DESCRIPTION
    Ensures required environment variables are set and launches Docker Compose services.
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Detach
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "==> Preparing Free Point local demo stack..." -ForegroundColor Cyan

if (-not $env:INTERNAL_MCP_SECRET) {
    $env:INTERNAL_MCP_SECRET = "local-dev-mcp-secret-32-chars-long-minimum"
    Write-Host "    Using default INTERNAL_MCP_SECRET for local dev" -ForegroundColor DarkGray
}

$composeArgs = @("compose", "-f", (Join-Path $RepoRoot "docker-compose.yml"), "up")
if ($Build) {
    $composeArgs += "--build"
}
if ($Detach) {
    $composeArgs += "-d"
}

Write-Host "==> Running: docker $($composeArgs -join ' ')" -ForegroundColor Cyan
& docker @composeArgs
