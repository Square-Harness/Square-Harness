#!/usr/bin/env bash
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
env_file="$repo_root/deployment/compose.local.env.example"

fail() {
  printf 'Square Harness: %s\n' "$1" >&2
  exit "${2:-1}"
}

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' \
    'Docker is not installed or is not on PATH.' \
    'Install Docker Desktop (Windows/macOS) or Docker Engine with Compose (Linux):' \
    'https://docs.docker.com/get-docker/' >&2
  exit 127
fi

if ! docker info >/dev/null 2>&1; then
  fail 'Docker is installed but its engine is not available. Start Docker Desktop/Engine and try again.'
fi

if ! docker compose version >/dev/null 2>&1; then
  fail 'The Docker Compose plugin is unavailable. Update Docker Desktop or install the Compose plugin.'
fi

[[ -f "$env_file" ]] || fail 'The local Compose configuration is missing from this download.'
cd "$repo_root"

if ! SQUARE_HARNESS_MODE=local docker compose --env-file "$env_file" up --build -d; then
  fail 'Docker could not start Square Harness. Check Docker Desktop/Engine, available disk space and port 8765.'
fi

printf '%s\n' 'Waiting for Square Harness to start...'
open_url=''
for _ in {1..60}; do
  logs="$(docker compose --env-file "$env_file" logs --no-color --no-log-prefix --tail=200 square-harness 2>/dev/null || true)"
  open_url="$(printf '%s\n' "$logs" | sed -n 's/.*Open: \(http:\/\/[^[:space:]]*\).*/\1/p' | tail -n 1)"
  [[ -n "$open_url" ]] && break
  sleep 2
done

if [[ -z "$open_url" ]]; then
  printf '%s\n' \
    'Square Harness did not provide its private app link.' \
    'Check the container with: docker compose --env-file deployment/compose.local.env.example ps' \
    'For help, inspect its logs locally; do not share logs containing the private app link.'
  exit 1
fi

printf '%s\n' 'Square Harness is running. Opening its private local app link.'
case "$(uname -s)" in
  Darwin)
    if ! open "$open_url" >/dev/null 2>&1; then
      printf 'Open this private link in your browser: %s\n' "$open_url"
    fi
    ;;
  Linux)
    if command -v xdg-open >/dev/null 2>&1; then
      if ! xdg-open "$open_url" >/dev/null 2>&1; then
        printf 'Open this private link in your browser: %s\n' "$open_url"
      fi
    else
      printf 'Open this private link in your browser: %s\n' "$open_url"
    fi
    ;;
  *)
    printf 'Open this private link in your browser: %s\n' "$open_url"
    ;;
esac
printf '%s\n' 'Do not share the private link; it grants access to this local workspace.'
