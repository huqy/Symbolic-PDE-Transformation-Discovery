#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == -B && "${2:-}" == -m && "${3:-}" == reproduce.s0_launcher ]]; then
    shift 3
    exec "$P13_RELEASE_PYTHON" -B -m reproduce.release_entry "$@"
fi
exec "$P13_RELEASE_PYTHON" "$@"
