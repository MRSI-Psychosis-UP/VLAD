#!/usr/bin/env bash
# Bootstrap syntax intentionally works with Apple's Bash 3.
set -euo pipefail

VLAD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CALLER_DIR="$PWD"
UV_VERSION="0.13.7"
REPORTS_ONLY=false
SKIP_SYSTEM=false
SKIP_FSL=false
NO_CHECK=false
ASSUME_YES=false
PYTHON_BIN="${VLAD_BOOTSTRAP_PYTHON:-}"
FSL_DEST=""
FSL_EXPLICIT=false
TEMP_DIR=""

fail() { echo "Installation failed: $*" >&2; exit 1; }
say() { echo "VLAD: $*"; }
cleanup() { [[ -z "$TEMP_DIR" ]] || rm -rf "$TEMP_DIR"; }
trap cleanup EXIT

usage() {
    cat <<'HELP'
Usage: ./install.sh [OPTIONS]

Install the complete Python/FSL/SwE workflow and configure ./vlad.
Missing system prerequisites use apt (Debian/Ubuntu) or Homebrew (macOS).
Existing working FSL installations are reused; no shell profiles are edited.

  --reports-only      Install Python reports/browser without FSL or desktop tools
  --python PATH       Select Python 3.10–3.12 (Tk required for the full workflow)
  --fsl-dir PATH      Reuse FSL here, or install FSL into this NEW directory
  --skip-system       Do not install system packages (use existing prerequisites)
  --skip-fsl          Do not download FSL; full installation still checks it
  --no-check          Skip final dependency checks; installation is unverified
  --yes              Use noninteractive package-manager/bootstrap operation
  -h, --help          Show this help
HELP
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reports-only) REPORTS_ONLY=true; SKIP_FSL=true; shift ;;
        --skip-system) SKIP_SYSTEM=true; shift ;;
        --skip-fsl) SKIP_FSL=true; shift ;;
        --no-check) NO_CHECK=true; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        --python|--fsl-dir)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 requires a path."
            if [[ "$1" == --python ]]; then PYTHON_BIN="$2";
            else FSL_DEST="$2"; FSL_EXPLICIT=true; fi
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --with-r) fail "Reports now use Python. Run ./install.sh without --with-r." ;;
        *) usage >&2; fail "Unknown option: $1" ;;
    esac
done

absolute_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$CALLER_DIR" "$1" ;;
    esac
}
if [[ -n "$PYTHON_BIN" ]]; then
    if [[ "$PYTHON_BIN" != */* ]]; then
        PYTHON_BIN="$(command -v "$PYTHON_BIN" || true)"
        [[ -n "$PYTHON_BIN" ]] || fail "Selected Python was not found."
    fi
    PYTHON_BIN="$(absolute_path "$PYTHON_BIN")"
fi
[[ -z "$FSL_DEST" ]] || FSL_DEST="$(absolute_path "$FSL_DEST")"

[[ -f "${VLAD_ROOT}/pyproject.toml" && -f "${VLAD_ROOT}/uv.lock" ]] || fail "Run this installer from a complete VLAD source checkout/archive."
cd "$VLAD_ROOT"
mkdir -p "${VLAD_ROOT}/.local" "${VLAD_ROOT}/.cache"
if [[ -z "${MPLCONFIGDIR:-}" ]]; then
    mkdir -p "${VLAD_ROOT}/.cache/matplotlib"
    export MPLCONFIGDIR="${VLAD_ROOT}/.cache/matplotlib"
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-${VLAD_ROOT}/.cache/uv}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-${VLAD_ROOT}/.local/python}"
TEMP_DIR="$(mktemp -d "${VLAD_ROOT}/.cache/install.XXXXXX")"

download() {
    local url="$1" output="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fLsS --retry 3 --connect-timeout 30 "$url" -o "$output" || fail "Download failed: $url. Nothing from this download was executed."
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$url" -O "$output" || fail "Download failed: $url. Nothing from this download was executed."
    else
        fail "curl or wget is required. Run without --skip-system to install prerequisites."
    fi
    [[ -s "$output" ]] || fail "Downloaded file is empty: $url."
}

as_admin() {
    if [[ "$EUID" -eq 0 ]]; then "$@";
    elif command -v sudo >/dev/null 2>&1; then sudo "$@";
    else fail "System packages need administrator access. Install sudo or use --skip-system with prerequisites already installed."; fi
}

ensure_system() {
    [[ "$SKIP_SYSTEM" == false ]] || return 0
    local system
    system="$(uname -s)"
    case "$system" in
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                local packages=(ca-certificates curl bzip2 libgomp1 libstdc++6 fonts-dejavu-core)
                # Tk comes with the selected/downloaded application Python.
                [[ "$REPORTS_ONLY" == true ]] || packages+=(bash bc zenity libx11-6)
                local missing=() package
                for package in "${packages[@]}"; do
                    if ! command -v dpkg-query >/dev/null 2>&1 ||
                       [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != "install ok installed" ]]; then
                        missing+=("$package")
                    fi
                done
                if [[ ${#missing[@]} -gt 0 ]]; then
                    say "Installing system prerequisites: ${missing[*]} (administrator access may be requested)."
                    as_admin apt-get update
                    as_admin env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
                fi
            elif [[ "$REPORTS_ONLY" == false ]] &&
                 { ! command -v zenity >/dev/null 2>&1 || ! command -v bc >/dev/null 2>&1; }; then
                fail "Automatic system setup supports Debian/Ubuntu or macOS. Install Bash 4+, bc, Zenity and X11 libraries, then rerun with --skip-system."
            fi ;;
        Darwin)
            # Reports can use the downloaded Python and Apple's existing Bash.
            [[ "$REPORTS_ONLY" == false ]] || return 0
            local brew_bin="$(command -v brew || true)"
            if [[ -z "$brew_bin" ]]; then
                for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
                    if [[ -x "$candidate" ]]; then brew_bin="$candidate"; break; fi
                done
            fi
            if [[ -z "$brew_bin" ]]; then
                say "Installing Homebrew for the native macOS prerequisites."
                download https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh "${TEMP_DIR}/homebrew.sh"
                if [[ "$ASSUME_YES" == true ]]; then
                    NONINTERACTIVE=1 /bin/bash "${TEMP_DIR}/homebrew.sh"
                else
                    /bin/bash "${TEMP_DIR}/homebrew.sh"
                fi
                for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
                    if [[ -x "$candidate" ]]; then brew_bin="$candidate"; break; fi
                done
                [[ -n "$brew_bin" ]] || fail "Homebrew was not installed successfully."
            fi
            local formulas=(bash bc zenity python@3.12 python-tk@3.12)
            local needed=() formula
            for formula in "${formulas[@]}"; do
                "$brew_bin" list --versions "$formula" >/dev/null 2>&1 || needed+=("$formula")
            done
            if [[ ${#needed[@]} -gt 0 ]]; then
                HOMEBREW_NO_AUTO_UPDATE=1 "$brew_bin" install "${needed[@]}"
            fi
            export PATH="$("$brew_bin" --prefix)/bin:${PATH}"
            ;;
        *) fail "Native installation supports Linux and macOS. On Windows run this installer inside a supported Linux/WSL environment." ;;
    esac
}

ensure_system
INSTALL_BASH="$(command -v bash || true)"
[[ -n "$INSTALL_BASH" ]] || fail "Bash was not found."
INSTALL_BASH="$(absolute_path "$INSTALL_BASH")"
if [[ "$REPORTS_ONLY" == false ]]; then
    "$INSTALL_BASH" -c '(( BASH_VERSINFO[0] >= 4 ))' || fail "Bash 4+ is needed; rerun without --skip-system."
    for command in bc zenity; do
        command -v "$command" >/dev/null 2>&1 || fail "Missing $command; rerun without --skip-system or choose --reports-only."
    done
fi

UV_BIN="${VLAD_ROOT}/.tools/uv/bin/uv"
uv_version_ok() {
    local version
    [[ -x "$1" ]] || return 1
    version="$("$1" --version 2>/dev/null)" || return 1
    [[ "$version" == "uv ${UV_VERSION}" || "$version" == "uv ${UV_VERSION} "* ]]
}
if command -v uv >/dev/null 2>&1 && uv_version_ok "$(command -v uv)"; then
    UV_BIN="$(command -v uv)"
elif ! uv_version_ok "$UV_BIN"; then
    say "Installing uv ${UV_VERSION} locally."
    download "https://astral.sh/uv/${UV_VERSION}/install.sh" "${TEMP_DIR}/uv.sh"
    UV_UNMANAGED_INSTALL="${VLAD_ROOT}/.tools/uv/bin" sh "${TEMP_DIR}/uv.sh"
fi
uv_version_ok "$UV_BIN" || fail "Pinned uv ${UV_VERSION} is unavailable."

supported_python() {
    [[ -x "$1" ]] && "$1" -c 'import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] < (3, 13)))' >/dev/null 2>&1
}
working_tk() { "$1" -c 'import tkinter; tkinter.Tcl()' >/dev/null 2>&1; }
if [[ -n "$PYTHON_BIN" ]]; then
    supported_python "$PYTHON_BIN" || fail "--python must select an executable Python 3.10–3.12."
    if [[ "$REPORTS_ONLY" == false ]] && ! working_tk "$PYTHON_BIN"; then
        fail "Selected Python has no working Tk. Omit --python to select/install a compatible runtime, or use --reports-only."
    fi
else
    for candidate in "${VLAD_ROOT}/.venv/bin/python" python3.12 python3.11 python3.10 python3; do
        candidate_path="$(command -v "$candidate" || true)"
        [[ -n "$candidate_path" ]] || continue
        if supported_python "$candidate_path" && { [[ "$REPORTS_ONLY" == true ]] || working_tk "$candidate_path"; }; then
            PYTHON_BIN="$(absolute_path "$candidate_path")"
            break
        fi
    done
    if [[ -z "$PYTHON_BIN" ]]; then
        say "Installing a private Python 3.12 runtime."
        "$UV_BIN" python install 3.12 --no-bin
        PYTHON_BIN="$("$UV_BIN" python find --managed-python --no-project 3.12)"
        supported_python "$PYTHON_BIN" || fail "Downloaded Python is unavailable."
        if [[ "$REPORTS_ONLY" == false ]] && ! working_tk "$PYTHON_BIN"; then
            fail "Downloaded Python has no working Tk on this platform. Install a matching Python/Tk distribution and use --python PATH."
        fi
    fi
fi
say "Using Python: $PYTHON_BIN"

if [[ -f "${VLAD_ROOT}/.local/runtime.env" ]]; then
    source "${VLAD_ROOT}/.local/runtime.env"
fi
if [[ "$FSL_EXPLICIT" == false ]]; then
    FSL_DEST="${FSLDIR:-${VLAD_INSTALL_FSLDIR:-}}"
    if [[ -z "$FSL_DEST" ]]; then
        fsl_command="$(command -v fslmaths || true)"
        if [[ "$fsl_command" == */share/fsl/bin/fslmaths ]]; then
            FSL_DEST="${fsl_command%/share/fsl/bin/fslmaths}"
        fi
    fi
    if [[ -z "$FSL_DEST" && -f "${HOME}/fsl/etc/fslconf/fsl.sh" ]]; then FSL_DEST="${HOME}/fsl"; fi
    [[ -n "$FSL_DEST" ]] || FSL_DEST="${VLAD_ROOT}/.local/fsl"
fi
FSL_DEST="$(absolute_path "$FSL_DEST")"
if [[ "$REPORTS_ONLY" == false ]]; then
    if [[ ! -f "${FSL_DEST}/etc/fslconf/fsl.sh" ]]; then
        if [[ "$SKIP_FSL" == true ]]; then fail "FSL is unavailable at $FSL_DEST and --skip-fsl forbids installation."; fi
        if [[ -e "$FSL_DEST" || -L "$FSL_DEST" ]]; then
            fail "Existing FSL directory is incomplete: $FSL_DEST. It will not be overwritten. Select a new --fsl-dir or repair it."
        fi
        [[ "$FSL_DEST" != *[[:space:]]* ]] || fail "The upstream FSL installer needs a destination without spaces. Use --fsl-dir /path/without-spaces."
        mkdir -p "$(dirname "$FSL_DEST")"
        [[ -w "$(dirname "$FSL_DEST")" ]] || fail "The FSL destination parent is not writable. Select a user-writable --fsl-dir."
        say "Installing FSL, including Randomise and SwE, into $FSL_DEST. This is a large download."
        download https://fsl.fmrib.ox.ac.uk/fsldownloads/fslconda/releases/fslinstaller.py "${TEMP_DIR}/fslinstaller.py"
        "$PYTHON_BIN" "${TEMP_DIR}/fslinstaller.py" -d "$FSL_DEST" -n -r -c none
    else
        say "Reusing FSL: $FSL_DEST"
    fi
    export FSLDIR="$FSL_DEST"
    source "${FSLDIR}/etc/fslconf/fsl.sh"
    export PATH="${FSLDIR}/share/fsl/bin:${INSTALL_BASH%/*}:${PATH}"
    for command in fslmaths fslmerge fslsplit fslstats fslval fslnvols fslmeants flirt Text2Vest swe; do
        [[ -x "${FSLDIR}/share/fsl/bin/${command}" ]] || fail "Existing/installed FSL lacks $command. Select a complete FSL installation with --fsl-dir; no existing files were replaced."
    done
    if [[ ! -x "${FSLDIR}/share/fsl/bin/randomise" && ! -x "${FSLDIR}/share/fsl/bin/randomise_parallel" ]]; then
        fail "FSL does not provide randomise or randomise_parallel."
    fi
elif [[ ! -f "${FSL_DEST}/etc/fslconf/fsl.sh" ]]; then
    FSL_DEST=""
fi

say "Installing the locked Python environment."
"$UV_BIN" sync --locked --python "$PYTHON_BIN" --no-dev
[[ -x "${VLAD_ROOT}/.venv/bin/vlad" && -x "${VLAD_ROOT}/.venv/bin/python" ]] || fail "Python installation did not produce the VLAD executable."

# Atomic path recording, separate from cohort configuration and shell profiles.
runtime_file="$(mktemp "${VLAD_ROOT}/.local/runtime.env.XXXXXX")"
{
    echo '# Generated by VLAD install.sh; contains installation paths only.'
    printf 'VLAD_INSTALL_BASH=%q\n' "$INSTALL_BASH"
    printf 'VLAD_INSTALL_FSLDIR=%q\n' "$FSL_DEST"
} >"$runtime_file"
chmod 600 "$runtime_file"
mv "$runtime_file" "${VLAD_ROOT}/.local/runtime.env"

profile=full
[[ "$REPORTS_ONLY" == false ]] || profile=reports
if [[ "$NO_CHECK" == false ]]; then
    export VLAD_PYTHON_BIN="${VLAD_ROOT}/.venv/bin/python"
    "${VLAD_ROOT}/.venv/bin/vlad" doctor --profile "$profile" || {
        status=$?
        echo 'Dependency checks failed; installation is incomplete. Review the missing checks above.' >&2
        exit "$status"
    }
    if [[ "$REPORTS_ONLY" == true ]]; then say "Python reporting is ready.";
    else say "Full Python/FSL/SwE installation is ready."; fi
else
    say "Installed; dependency checks were skipped. This installation is unverified."
fi
printf 'Run without activation: %q --help\n' "${VLAD_ROOT}/vlad"
printf 'Recheck dependencies: %q doctor --profile %s\n' "${VLAD_ROOT}/vlad" "${profile:-full}"
