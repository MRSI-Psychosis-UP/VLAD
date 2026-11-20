#!/usr/bin/env bash
# Compatibility launcher; source lives in workflows/.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/workflows/Prep_VLAD_b6.1.sh" "$@"
