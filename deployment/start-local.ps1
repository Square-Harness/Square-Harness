# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repoRoot 'deployment/compose.local.env.example'
$useDev = $false
$devSha = $null

if ($args.Count -gt 0) {
    if ($args[0] -in @('-h', '--help')) {
        Write-Output 'Usage: powershell.exe -File deployment/start-local.ps1 [--dev [12-character-SHA]]'
        Write-Output ''
        Write-Output 'Without --dev, start the latest stable image.'
        Write-Output '--dev starts the most recently completed successful non-main build.'
        Write-Output '--dev <SHA> starts the development image built from that exact commit.'
        Write-Output 'Development mode uses a separate data volume unless SQUARE_DATA_VOLUME is set.'
        exit 0
    }

    if ($args[0] -ne '--dev' -or $args.Count -gt 2) {
        [Console]::Error.WriteLine('Usage: deployment/start-local.ps1 [--dev [12-character-SHA]]')
        exit 64
    }

    $useDev = $true
    if ($args.Count -eq 2) {
        if ($args[1] -notmatch '^[0-9a-fA-F]{12}$') {
            [Console]::Error.WriteLine('--dev requires exactly 12 hexadecimal characters when a SHA is supplied.')
            exit 64
        }
        $devSha = $args[1].ToLowerInvariant()
    }
}

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
$previousImage = [Environment]::GetEnvironmentVariable('SQUARE_HARNESS_IMAGE', 'Process')
$previousVolume = [Environment]::GetEnvironmentVariable('SQUARE_DATA_VOLUME', 'Process')
try {
    $env:SQUARE_HARNESS_MODE = 'local'
    if ($useDev) {
        if ($devSha) {
            $env:SQUARE_HARNESS_IMAGE = "ghcr.io/square-harness/square-harness-dev:dev-$devSha"
        }
        else {
            $env:SQUARE_HARNESS_IMAGE = 'ghcr.io/square-harness/square-harness-dev:dev'
        }
        if ([string]::IsNullOrWhiteSpace($env:SQUARE_DATA_VOLUME)) {
            $env:SQUARE_DATA_VOLUME = 'square-harness-dev-data'
        }
        Write-Host "Using development image: $env:SQUARE_HARNESS_IMAGE"
    }
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
    if ($null -eq $previousImage) {
        Remove-Item Env:SQUARE_HARNESS_IMAGE -ErrorAction SilentlyContinue
    }
    else {
        $env:SQUARE_HARNESS_IMAGE = $previousImage
    }
    if ($null -eq $previousVolume) {
        Remove-Item Env:SQUARE_DATA_VOLUME -ErrorAction SilentlyContinue
    }
    else {
        $env:SQUARE_DATA_VOLUME = $previousVolume
    }
}
