#!/usr/bin/env bash
# Compatibility entry point for the original Python-only setup command.
set -e
VLAD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "${VLAD_ROOT}/scripts/install.sh" --reports-only "$@"
