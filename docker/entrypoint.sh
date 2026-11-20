#!/usr/bin/env bash
set -e

if [[ -n "${FSLDIR:-}" && -f "${FSLDIR}/etc/fslconf/fsl.sh" ]]; then
    # shellcheck disable=SC1091
    source "${FSLDIR}/etc/fslconf/fsl.sh"
fi

export VLAD_PYTHON_BIN="${VLAD_PYTHON_BIN:-/opt/vlad/.venv/bin/python}"
export FSLOUTPUTTYPE="${FSLOUTPUTTYPE:-NIFTI_GZ}"

cd /opt/vlad

case "${1:-}" in
    vlad)
        shift
        # Preserve the previous image's `vlad -c ...` invocation.
        if [[ "${1:-}" == -* && "${1:-}" != --help && "${1:-}" != -h && "${1:-}" != --version ]]; then
            set -- run "$@"
        fi
        exec /opt/vlad/.venv/bin/vlad "$@"
        ;;
    prep)
        shift
        exec /opt/vlad/.venv/bin/vlad prep "$@"
        ;;
    run | doctor | report | gui)
        exec /opt/vlad/.venv/bin/vlad "$@"
        ;;
    python)
        shift
        exec "${VLAD_PYTHON_BIN}" "$@"
        ;;
    "")
        exec bash
        ;;
    *)
        exec "$@"
        ;;
esac
