#!/usr/bin/env bash
# Shared runtime paths. Keep caller cwd so relative command-line paths still work.
VLAD_HOME="${VLAD_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VLAD_HOME="$(cd "$VLAD_HOME" && pwd)" || exit 1
export VLAD_HOME

# Written by install.sh. This file contains paths, never study configuration.
if [[ -f "${VLAD_HOME}/.local/runtime.env" ]]; then
    source "${VLAD_HOME}/.local/runtime.env" || exit 1
fi

if (( BASH_VERSINFO[0] < 4 )) && [[ "${VLAD_RUNTIME_PYTHON_ONLY:-false}" != true ]]; then
    # The compatibility launchers may start Apple's system Bash 3. Re-enter
    # the actual workflow with the modern Bash selected during installation.
    if [[ -n "${VLAD_INSTALL_BASH:-}" && -x "$VLAD_INSTALL_BASH" &&
          -n "${BASH_SOURCE[1]:-}" && -f "${BASH_SOURCE[1]}" ]] &&
       "$VLAD_INSTALL_BASH" -c '(( BASH_VERSINFO[0] >= 4 ))'; then
        exec "$VLAD_INSTALL_BASH" "${BASH_SOURCE[1]}" "$@"
    fi
    echo "VLAD requires Bash 4 or newer (macOS: install Bash with Homebrew)." >&2
    echo "Run ${VLAD_HOME}/install.sh to configure the required runtime." >&2
    exit 1
fi

if [[ -n "${VLAD_INSTALL_BASH:-}" && -x "$VLAD_INSTALL_BASH" &&
      "$(command -v bash || true)" != "$VLAD_INSTALL_BASH" ]]; then
    export PATH="${VLAD_INSTALL_BASH%/*}:${PATH}"
fi
if [[ -z "${FSLDIR:-}" && -n "${VLAD_INSTALL_FSLDIR:-}" ]]; then
    export FSLDIR="$VLAD_INSTALL_FSLDIR"
fi
if [[ -n "${FSLDIR:-}" ]]; then
    [[ "$FSLDIR" = /* ]] || FSLDIR="${PWD}/${FSLDIR}"
    export FSLDIR
    vlad_runtime_output_type="${FSLOUTPUTTYPE:-}"
    if [[ -f "${FSLDIR}/etc/fslconf/fsl.sh" &&
          "${VLAD_FSL_INITIALIZED_FOR:-}" != "$FSLDIR" ]]; then
        if ! source "${FSLDIR}/etc/fslconf/fsl.sh"; then
            echo "Could not initialise FSL at $FSLDIR. Re-run ${VLAD_HOME}/install.sh." >&2
            exit 1
        fi
        export VLAD_FSL_INITIALIZED_FOR="$FSLDIR"
    fi
    # Use FSL's wrappers; its bin/ contains Python/Conda executables that must
    # not replace the dedicated VLAD interpreter or other user commands.
    if [[ "$PATH" != "${FSLDIR}/share/fsl/bin" &&
          "$PATH" != "${FSLDIR}/share/fsl/bin:"* ]]; then
        export PATH="${FSLDIR}/share/fsl/bin:${PATH}"
    fi
    if [[ -n "$vlad_runtime_output_type" ]]; then
        export FSLOUTPUTTYPE="$vlad_runtime_output_type"
    fi
fi
export FSLOUTPUTTYPE="${FSLOUTPUTTYPE:-NIFTI_GZ}"

VLAD_CONFIG_FILE="${VLAD_CONFIG_FILE:-${VLAD_HOME}/VLAD_configs.txt}"
VLAD_BATCH_FILE="${VLAD_BATCH_FILE:-${VLAD_HOME}/VLAD_batch_commands.txt}"
# Resolve overrides before preparation/analysis changes into the cohort directory.
[[ "$VLAD_CONFIG_FILE" = /* ]] || VLAD_CONFIG_FILE="${PWD}/${VLAD_CONFIG_FILE}"
[[ "$VLAD_BATCH_FILE" = /* ]] || VLAD_BATCH_FILE="${PWD}/${VLAD_BATCH_FILE}"
export VLAD_CONFIG_FILE VLAD_BATCH_FILE

if [[ -z "${VLAD_PYTHON_BIN:-}" && -z "${VLAD_PYTHON_ENV:-}" ]]; then
    if [[ -x "${VLAD_HOME}/.venv/bin/python" ]]; then
        VLAD_PYTHON_BIN="${VLAD_HOME}/.venv/bin/python"
    else
        VLAD_PYTHON_BIN="$(command -v python3 || true)"
    fi
fi
if [[ -n "${VLAD_PYTHON_BIN:-}" && "$VLAD_PYTHON_BIN" != /* ]]; then
    VLAD_PYTHON_BIN="$(command -v "$VLAD_PYTHON_BIN" || true)"
    if [[ -n "$VLAD_PYTHON_BIN" && "$VLAD_PYTHON_BIN" != /* ]]; then
        VLAD_PYTHON_BIN="${PWD}/${VLAD_PYTHON_BIN}"
    fi
fi
export VLAD_PYTHON_BIN

# Resolve old same-checkout helper paths recorded in cohort configurations.
# Custom scripts elsewhere, including intentionally missing paths, stay intact.
vlad_resolve_helper_path() {
    local configured="$1"
    local fallback="$2"
    if [[ -z "$configured" ||
          ( "$configured" == "${VLAD_HOME}/$(basename "$fallback")" && ! -e "$configured" ) ]]; then
        printf '%s\n' "$fallback"
    else
        printf '%s\n' "$configured"
    fi
}

vlad_require_separate_output() {
    local output input ancestor="$1"
    while [[ ! -d "$ancestor" ]]; do
        [[ ! -e "$ancestor" && ! -L "$ancestor" ]] || return 1
        ancestor=$(dirname "$ancestor") || return 1
    done
    output=$(cd "$ancestor" && pwd -P) || return 1
    input=$(cd "$2" && pwd -P) || return 1
    if [[ "$output" == "$input" || "$output/" == "$input/"* ]]; then
        printf 'Choose a VLAD output folder outside the input derivative folder %s.\n' "$input" >&2
        return 1
    fi
}

# Keep the plotting cache with a writable checkout; respect explicit settings.
if [[ -z "${MPLCONFIGDIR:-}" ]] && mkdir -p "${VLAD_HOME}/.cache/matplotlib" 2>/dev/null; then
    export MPLCONFIGDIR="${VLAD_HOME}/.cache/matplotlib"
fi
