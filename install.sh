#!/usr/bin/env bash
# One-command installation from a checkout or unpacked source distribution.
set -e
VLAD_INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${VLAD_INSTALL_ROOT}/scripts/install.sh" "$@"
