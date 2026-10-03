#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/_entry.sh"
exec "$P13_PYTHON" -B -m reproduce.s2_launcher "$@"
