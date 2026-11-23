#!/bin/sh
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

set -eu

mode="${SQUARE_HARNESS_MODE:-local}"
port=8765

launch() {
  exec python /usr/local/bin/square-harness-launch "$@"
}

case "$mode" in
  local)
    data_dir="${SQUARE_DATA_DIR:-/data/local}"
    workspace="$data_dir/workspaces/default"
    gui_root="$data_dir/workspaces"
    mkdir -p "$workspace" "$gui_root"
    launch square-harness --gui --no-browser --gui-host 0.0.0.0 --gui-port "$port" \
      --workspace "$workspace" --gui-root "$gui_root" "$@"
    ;;
  server)
    data_root="${SQUARE_SERVER_DATA_ROOT:-/data/server}"
    mkdir -p "$data_root"
    launch square-harness --gui --no-browser --gui-host 0.0.0.0 --gui-port "$port" \
      --workspace "$data_root" --gui-root "$data_root" "$@"
    ;;
  *)
    printf '%s\n' 'SQUARE_HARNESS_MODE must be local or server.' >&2
    exit 64
    ;;
esac
