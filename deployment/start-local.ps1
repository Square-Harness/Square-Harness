# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repoRoot 'deployment/compose.local.env.example'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'Docker is not installed or is not on PATH. Install Docker Desktop: https://docs.docker.com/get-docker/'
    exit 127
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Docker is installed but its engine is not available. Start Docker Desktop and try again.'
    exit 1
}

& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'The Docker Compose plugin is unavailable. Update Docker Desktop and try again.'
    exit 1
}

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    Write-Error 'The local Compose configuration is missing from this download.'
    exit 1
}

$previousMode = [Environment]::GetEnvironmentVariable('SQUARE_HARNESS_MODE', 'Process')
try {
    $env:SQUARE_HARNESS_MODE = 'local'
    Push-Location $repoRoot
    try {
        & docker compose --env-file $envFile pull
        if ($LASTEXITCODE -ne 0) {
            Write-Error 'Could not download the Square Harness image. Check your internet connection and confirm that the selected GHCR image is available to you.'
            exit $LASTEXITCODE
        }

        & docker compose --env-file $envFile up -d
        if ($LASTEXITCODE -ne 0) {
            Write-Error 'Docker could not start Square Harness. Check Docker Desktop, available disk space and port 8765.'
            exit $LASTEXITCODE
        }

        Write-Host 'Waiting for Square Harness to start...'
        $openUrl = $null
        for ($attempt = 0; $attempt -lt 60 -and -not $openUrl; $attempt++) {
            $logs = & docker compose --env-file $envFile logs --no-color --no-log-prefix --tail=200 square-harness 2>$null
            $match = [regex]::Match(($logs -join "`n"), 'Open:\s+(http://\S+)')
            if ($match.Success) {
                $openUrl = $match.Groups[1].Value
                break
            }
            Start-Sleep -Seconds 2
        }

        if (-not $openUrl) {
            Write-Error 'Square Harness did not provide its private app link. Check container status with: docker compose --env-file deployment/compose.local.env.example ps'
            exit 1
        }

        Write-Host 'Square Harness is running. Opening its private local app link.'
        try {
            Start-Process -FilePath $openUrl
        }
        catch {
            Write-Host "Open this private link in your browser: $openUrl"
        }
        Write-Host 'Do not share the private link; it grants access to this local workspace.'
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -eq $previousMode) {
        Remove-Item Env:SQUARE_HARNESS_MODE -ErrorAction SilentlyContinue
    }
    else {
        $env:SQUARE_HARNESS_MODE = $previousMode
    }
}
