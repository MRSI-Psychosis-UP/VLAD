#!/usr/bin/env bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: vlad prep [--b0 3|7] [--overwrite] [--config PATH --modality MRSI|DTI|STRUCTURAL --non-interactive [--subjects PATH]]"
    echo "Interactive cohort setup; requires FSL, Python with Tk, zenity and a display."
    echo "Registry: $VLAD_CONFIG_FILE (override with VLAD_CONFIG_FILE)."
    exit 0
fi

if [[ -n "${VLAD_PYTHON_ENV:-}" && -z "${VLAD_PYTHON_BIN:-}" ]]; then
    pyenv_prefix=$(pyenv prefix "$VLAD_PYTHON_ENV") || exit 1
    VLAD_PYTHON_BIN="${pyenv_prefix}/bin/python"
fi

METABSUM3T=("NAANAAG" "CrPCr" "GPCPCh" "Ins" "GluGln")
METABSUM7T5=("NAANAAG" "GluGln")
METABSUM7T9=("NAA" "NAAG" "CrPCr" "Glu" "Gln" "Ins" "GPCPCh" "GSH" "GABA")

OVERWRITE=false
B0_MODE="3"
NONINTERACTIVE=false
PREP_CONFIG=""
PREP_MODALITY=""
PREP_SUBJECTS=""
PREP_FAILED=false

while [[ $# -gt 0 ]]; do
    case "$1" in
    --non-interactive)
        NONINTERACTIVE=true
        shift
        ;;
    --config|--modality|--subjects)
        [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo "$1 requires a value." >&2; exit 1; }
        if [[ "$1" == --config ]]; then
            PREP_CONFIG="$2"
            [[ "$PREP_CONFIG" = /* ]] || PREP_CONFIG="${PWD}/${PREP_CONFIG}"
        elif [[ "$1" == --modality ]]; then
            PREP_MODALITY="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
        else
            PREP_SUBJECTS="$2"
            [[ "$PREP_SUBJECTS" = /* ]] || PREP_SUBJECTS="${PWD}/${PREP_SUBJECTS}"
        fi
        shift 2
        ;;
    --overwrite)
        OVERWRITE=true
        shift
        ;;
    --b0)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing value for $1 (use 3 or 7)."
            exit 1
        fi
        case "$2" in
        3 | 7)
            B0_MODE="$2"
            ;;
        *)
            echo "Invalid --b0 value: $2. Allowed: 3 or 7."
            exit 1
            ;;
        esac
        shift 2
        ;;
    *)
        break
        ;;
    esac
done

if [[ -n "$PREP_SUBJECTS" && "$NONINTERACTIVE" != true ]]; then
    echo "--subjects requires --config, --modality and --non-interactive for BIDS derivative preparation." >&2
    exit 1
fi

# current_dir="/Volumes/MindfulMRSI"
# NAME="bestofsubjects"
# CARDS="/Volumes/MindfulMRSI/Aligned"

# SUBJECTS=($(cat ${current_dir}/Listes/List_${NAME}.txt))

#CREATION OF SUBJECTS FILE FOR VLAD. HE WILL BASED ALL THE 4D cards on this. EACH FILE used SHOULD BE DONE HERE.

#Create an option to delete a previous configuration file

# WHAT WE NEED TO ADD :
# - Installation of zenity via homebrew
# - A tool to verify that your files are in good format
# - Installation of usefull packages : fsl, python environment, R packages, pandoc

##############################################################
################### FUNCTIONS ################################
##############################################################

name_subject_file() {
    local subject=$1
    local visit=$2
    local data_type=$3
    local bids_infix=$4
    local description=$5
    local compressed=$6

    subject=$(trim_whitespace "$subject")
    visit=$(trim_whitespace "$visit")
    data_type=$(trim_whitespace "$data_type")
    bids_infix=$(normalize_bids_infix "$bids_infix")
    description=$(trim_whitespace "$description")
    compressed=$(trim_whitespace "$compressed")

    if [[ $# -ne 6 ]]; then
        echo "name_subject_file badly used, this will cause some errors. Exiting"
        return 1
    fi

    local tag_suffix=""
    if [[ -n "$bids_infix" ]]; then
        tag_suffix="_${bids_infix}"
    fi

    echo "sub-${subject}_ses-${visit}${tag_suffix}_desc-${description}_${data_type}.nii${compressed}"

}

name_subject_file_mrsi() { # ADDED FOR THE NEW BIDS
    local subject=$1
    local visit=$2
    local data_type=$3
    local met=$4
    local description=$5
    local compressed=$6

    if [[ $# -ne 6 ]]; then
        echo "name_subject_file badly used, this will cause some errors. Exiting"
        return 1
    fi

    local space_label=${MRSI_SPACE_LABEL:-mni}
    local extra_tags=${MRSI_EXTRA_TAGS:-}
    subject=$(trim_whitespace "$subject")
    visit=$(trim_whitespace "$visit")
    data_type=$(trim_whitespace "$data_type")
    met=$(trim_whitespace "$met")
    description=$(trim_whitespace "$description")
    compressed=$(trim_whitespace "$compressed")
    space_label=$(trim_whitespace "$space_label")
    extra_tags=$(trim_whitespace "$extra_tags")
    local tag_suffix="_space-${space_label}"
    if [[ -n "$extra_tags" ]]; then
        for tag in $extra_tags; do
            tag_suffix+="_${tag}"
        done
    fi

    echo "sub-${subject}_ses-${visit}${tag_suffix}_met-${met}_desc-${description}_${data_type}.nii${compressed}"

}

name_subject_file_mrsi_spec() { # ADDED FOR THE NEW BIDS
    local subject=$1
    local visit=$2
    local data_type=$3
    #local met=$4
    local description=$4
    local compressed=$5

    if [[ $# -ne 5 ]]; then
        echo "name_subject_file badly used, this will cause some errors. Exiting"
        return 1
    fi

    local space_label=${MRSI_SPACE_LABEL:-mni}
    local extra_tags=${MRSI_EXTRA_TAGS:-}
    subject=$(trim_whitespace "$subject")
    visit=$(trim_whitespace "$visit")
    data_type=$(trim_whitespace "$data_type")
    description=$(trim_whitespace "$description")
    compressed=$(trim_whitespace "$compressed")
    space_label=$(trim_whitespace "$space_label")
    extra_tags=$(trim_whitespace "$extra_tags")
    local tag_suffix="_space-${space_label}"
    if [[ -n "$extra_tags" ]]; then
        for tag in $extra_tags; do
            tag_suffix+="_${tag}"
        done
    fi

    echo "sub-${subject}_ses-${visit}${tag_suffix}_desc-${description}_${data_type}.nii${compressed}"

}


should_create_file() {
    local target="$1"
    if [[ -f "$target" && ${OVERWRITE} != true ]]; then
        echo "File exists, skipping (use --overwrite to recompute): $target"
        return 1
    fi
    return 0
}

append_unique() {
    local arr_name="$1"
    local value="$2"
    local current=()
    eval "current=(\"\${${arr_name}[@]}\")"
    for v in "${current[@]}"; do
        if [[ "$v" == "$value" ]]; then
            return 0
        fi
    done
    current+=("$value")
    eval "${arr_name}=(\"\${current[@]}\")"
}

METABSUM_EXPORT=()
METABQUOTIENTS_EXPORT=()
METAB_EXPORT=()
PREPARED_MASK_PATHS=()
PARSED_MASK_ENTRIES=()
PREP_MASK_PYTHON=""

ensure_mask_python() {
    if [[ -n "$PREP_MASK_PYTHON" && -x "$PREP_MASK_PYTHON" ]]; then
        return 0
    fi

    local candidate="${VLAD_PYTHON_BIN:-}"
    if [[ -z "$candidate" ]]; then
        candidate=$(command -v python3 2>/dev/null || true)
    elif [[ "$candidate" != */* ]]; then
        candidate=$(command -v "$candidate" 2>/dev/null || true)
    fi

    if [[ -z "$candidate" || ! -x "$candidate" ]]; then
        echo "A Python 3 interpreter is required to validate and resample masks." >&2
        echo "Activate the VLAD Python environment or set VLAD_PYTHON_BIN=/path/to/python3." >&2
        return 1
    fi
    if ! "$candidate" -c 'import nibabel, nilearn, numpy' >/dev/null 2>&1; then
        echo "Python at '$candidate' is missing nibabel, nilearn, or numpy." >&2
        echo "Activate the VLAD Python environment or set VLAD_PYTHON_BIN to its Python interpreter." >&2
        return 1
    fi

    PREP_MASK_PYTHON="$candidate"
}

compare_nifti_files() {
    local file1="$1"
    local file2="$2"

    ensure_mask_python || return 2
    "$PREP_MASK_PYTHON" -c '
import sys
import nibabel as nib
import numpy as np

try:
    first = nib.load(sys.argv[1])
    second = nib.load(sys.argv[2])
except Exception as exc:
    print(f"Unable to read NIfTI geometry: {exc}", file=sys.stderr)
    raise SystemExit(2)

same_shape = first.shape[:3] == second.shape[:3]
same_affine = np.allclose(first.affine, second.affine, rtol=1e-5, atol=1e-5)
raise SystemExit(0 if same_shape and same_affine else 1)
' "$file1" "$file2"
}

trim_whitespace() {
    local str="$1"
    str="${str#"${str%%[![:space:]]*}"}"
    str="${str%"${str##*[![:space:]]}"}"
    printf '%s' "$str"
}

normalize_bids_infix() {
    local infix
    infix=$(trim_whitespace "$1")

    while [[ "$infix" == _* ]]; do
        infix="${infix#_}"
    done
    while [[ "$infix" == *_ ]]; do
        infix="${infix%_}"
    done

    printf '%s' "$infix"
}

build_bids_infix_from_parts() {
    local space_label
    local extra_tags
    local acquisition
    local infix

    space_label=$(trim_whitespace "${1:-mni}")
    extra_tags=$(trim_whitespace "${2:-}")
    acquisition=$(trim_whitespace "${3:-}")

    infix="space-${space_label}"
    if [[ -n "$extra_tags" ]]; then
        for tag in $extra_tags; do
            infix+="_${tag}"
        done
    fi
    if [[ -n "$acquisition" ]]; then
        infix+="_acq-${acquisition}"
    fi

    normalize_bids_infix "$infix"
}

resolve_bids_infix() {
    local explicit_infix
    explicit_infix=$(normalize_bids_infix "${1:-}")

    if [[ -n "$explicit_infix" ]]; then
        printf '%s' "$explicit_infix"
    else
        build_bids_infix_from_parts "${2:-mni}" "${3:-}" "${4:-}"
    fi
}

resolve_mask_source_path() {
    local entry="$1"
    local dest_dir="$2"
    local mask_path=""

    if [[ -f "$entry" ]]; then
        mask_path="$entry"
    elif [[ -n "$GLOBAL_DIR" && -f "${GLOBAL_DIR}/Masques/$entry" ]]; then
        mask_path="${GLOBAL_DIR}/Masques/$entry"
    elif [[ -n "$GLOBAL_DIR" && -d "${GLOBAL_DIR}/Masques" ]]; then
        local base_name
        base_name=$(basename "$entry")
        mask_path=$(find "${GLOBAL_DIR}/Masques" -type f -name "$base_name" ! -path "${dest_dir}/*" 2>/dev/null | head -n 1)
    fi

    if [[ -n "$mask_path" ]]; then
        printf '%s\n' "$mask_path"
    fi
}

is_nifti_path() {
    local lower_path="${1,,}"
    [[ "$lower_path" == *.nii || "$lower_path" == *.nii.gz ]]
}

parse_mask_entries() {
    local mask_input="$1"
    local lower_input="${mask_input,,}"
    local raw_entries=()
    local entry

    PARSED_MASK_ENTRIES=()
    if [[ "$mask_input" == *"|"* ]]; then
        IFS='|' read -r -a raw_entries <<<"$mask_input"
    elif [[ -f "$mask_input" && ( "$lower_input" == *.txt || "$lower_input" == *.list ) ]]; then
        while IFS= read -r entry || [[ -n "$entry" ]]; do
            entry=$(trim_whitespace "$entry")
            [[ -z "$entry" || "$entry" == \#* ]] && continue
            raw_entries+=("$entry")
        done <"$mask_input"
    else
        raw_entries=("$mask_input")
    fi

    for entry in "${raw_entries[@]}"; do
        entry=$(trim_whitespace "$entry")
        [[ -z "$entry" ]] && continue
        PARSED_MASK_ENTRIES+=("$entry")
    done
}

run_resample_and_copy_mask() {
    local mask_path="$1"
    local reference_image="$2"
    local destination="$3"

    if [[ -z "$script_dir" ]]; then
        echo "script_dir is not defined. Cannot call resample_masks_b3_replace.py"
        return 1
    fi
    ensure_mask_python || return 1

    local resample_output
    if ! resample_output=$("$PREP_MASK_PYTHON" "${PYTHON_HELPERS}/resample_masks_b3_replace.py" --masks "$mask_path" --t1 "$reference_image" --output "$destination" --interp nearest 2>&1); then
        echo "Resampling failed for $mask_path"
        echo "$resample_output"
        return 1
    fi

    if [[ ! -f "$destination" ]]; then
        echo "Resampling completed without creating the expected file: $destination"
        echo "$resample_output"
        return 1
    fi

    [[ -n "$resample_output" ]] && echo "$resample_output"
    echo "Resampled mask stored at $destination"
}

get_first_subject_entry() {
    if [[ -z "$LIST_ALL_SUBJECTS" || ! -f "$LIST_ALL_SUBJECTS" ]]; then
        return 1
    fi
    head -n 1 "$LIST_ALL_SUBJECTS"
}

get_reference_image_path() {
    local modality="$1"
    local desc="$2"

    local first_subject
    first_subject=$(get_first_subject_entry) || return 1

    local sub=${first_subject%_*}
    local visit=${first_subject#*_}
    local candidate=""
    local search_dir=""

    case "$modality" in
    mrsi)
        local met="${METAB[0]}"
        if [[ -z "$met" ]]; then
            echo "No metabolite configured, cannot build MRSI reference"
            return 1
        fi
        candidate=$(name_subject_file_mrsi "$sub" "$visit" "mrsi" "$met" "$desc" "$MRSI_COMPRESSED")
        search_dir="$NII_DIR_MRSI"
        ;;
    dti)
        local dtisuffix=${DTI_DATA_SUFFIX:-dwi}
        local dtibids
        dtibids=$(resolve_bids_infix "${DTI_BIDS_INFIX:-}" "${DTI_SPACE_LABEL:-mni}" "${DTI_EXTRA_TAGS:-}" "${DTI_ACQ_LABEL:-dti}")
        candidate=$(name_subject_file "$sub" "$visit" "$dtisuffix" "$dtibids" "$desc" "$DTI_COMPRESSED")
        search_dir="$NII_DIR_DTI"
        ;;
    structural)
        local structsuffix=${STRUCTURAL_DATA_SUFFIX:-T1w}
        local structbids
        structbids=$(resolve_bids_infix "${STRUCTURAL_BIDS_INFIX:-}" "${STRUCTURAL_SPACE_LABEL:-mni}" "${STRUCTURAL_EXTRA_TAGS:-}" "${STRUCTURAL_ACQ_LABEL:-memprage}")
        candidate=$(name_subject_file "$sub" "$visit" "$structsuffix" "$structbids" "$desc" "$STRUCTURAL_COMPRESSED")
        search_dir="$NII_DIR_STRUCTURAL"
        ;;
    esac

    if [[ -z "$search_dir" || ! -d "$search_dir" ]]; then
        return 1
    fi

    local reference_path
    reference_path=$(find "$search_dir" -type f -name "$candidate" 2>/dev/null | head -n 1)
    if [[ -n "$reference_path" ]]; then
        printf '%s\n' "$reference_path"
        return 0
    fi

    return 1
}

configure_masks_for_modality() {
    local modality="$1"
    local mask_list="$2"
    local reference_image="$3"

    if [[ -z "$mask_list" ]]; then
        echo "No masks specified for ${modality}, skipping."
        return 0
    fi

    if [[ -z "$reference_image" || ! -f "$reference_image" ]]; then
        echo "Reference image missing for ${modality}, unable to validate masks."
        return 1
    fi

    parse_mask_entries "$mask_list"
    local mask_entries=("${PARSED_MASK_ENTRIES[@]}")

    if [[ ${#mask_entries[@]} -eq 0 ]]; then
        echo "No valid masks specified for ${modality}." >&2
        return 1
    fi

    ensure_mask_python || return 1

    local masks_root="${GLOBAL_DIR}/Masques"
    local cohort_name="${COHORT:-default}"
    local dest_dir="${masks_root}/${cohort_name}/${modality}"
    if ! mkdir -p "$dest_dir"; then
        echo "Unable to create the mask directory: $dest_dir" >&2
        return 1
    fi

    echo "Preparing masks for ${modality} in $dest_dir"
    PREPARED_MASK_PATHS=()
    local preparation_failed=false

    for entry in "${mask_entries[@]}"; do
        entry=$(trim_whitespace "$entry")
        [[ -z "$entry" ]] && continue
        if ! is_nifti_path "$entry"; then
            echo "Mask entry '$entry' is not a .nii or .nii.gz file." >&2
            preparation_failed=true
            continue
        fi

        local mask_path
        mask_path=$(resolve_mask_source_path "$entry" "$dest_dir")
        if [[ -z "$mask_path" || ! -f "$mask_path" ]]; then
            echo "Mask entry '$entry' was not found." >&2
            preparation_failed=true
            continue
        fi

        local mask_name
        mask_name=$(basename "$mask_path")
        local destination="${dest_dir}/${mask_name}"

        if [[ "$mask_path" == "$destination" ]]; then
            PREPARED_MASK_PATHS+=("$destination")
            echo "Mask is already stored at $destination"
            continue
        fi

        if ! should_create_file "$destination"; then
            PREPARED_MASK_PATHS+=("$destination")
            continue
        fi

        compare_nifti_files "$mask_path" "$reference_image"
        local geometry_status=$?
        case "$geometry_status" in
        0)
            if cp "$mask_path" "$destination"; then
                echo "Copied already aligned mask ${mask_name} to ${destination}"
                PREPARED_MASK_PATHS+=("$destination")
            else
                echo "Unable to copy mask '$mask_path' to '$destination'." >&2
                preparation_failed=true
            fi
            ;;
        1)
            if run_resample_and_copy_mask "$mask_path" "$reference_image" "$destination"; then
                PREPARED_MASK_PATHS+=("$destination")
            else
                preparation_failed=true
            fi
            ;;
        *)
            echo "Unable to validate NIfTI geometry for '$mask_path'." >&2
            preparation_failed=true
            ;;
        esac
    done

    if [[ "$preparation_failed" == true ]]; then
        echo "One or more ${modality} masks could not be prepared." >&2
        return 1
    fi

    [[ ${#PREPARED_MASK_PATHS[@]} -gt 0 ]]
}

check_file_not_empty() {
    local nifti_file="$1"
    if [[ -z "$nifti_file" || ! -f "$nifti_file" ]]; then
        return 1
    fi

    local range_output
    range_output=$(fslstats "$nifti_file" -R 2>/dev/null)
    if [[ -z "$range_output" ]]; then
        return 1
    fi

    local min_val max_val
    read -r min_val max_val <<<"$range_output"

    local max_lower
    max_lower=$(echo "$max_val" | tr '[:upper:]' '[:lower:]')
    if [[ -z "$max_val" || "$max_lower" == "nan" ]]; then
        return 1
    fi

    if awk -v v="$max_val" 'BEGIN{exit !(v+0 == 0)}'; then
        return 1
    fi

    local mean_val_raw
    mean_val_raw=$(fslstats "$nifti_file" -m 2>/dev/null | tr -d '[:space:]')
    local mean_lower
    mean_lower=$(echo "$mean_val_raw" | tr '[:upper:]' '[:lower:]')
    if [[ -z "$mean_val_raw" || "$mean_lower" == "nan" ]]; then
        return 1
    fi

    return 0
}

# Return a usable starting directory for graphical file choosers.
dialog_start_directory() {
    local requested_path="$1"

    if [[ -d "$requested_path" ]]; then
        printf '%s\n' "$requested_path"
    elif [[ -n "$requested_path" && -d "$(dirname "$requested_path")" ]]; then
        dirname "$requested_path"
    elif [[ -n "$GLOBAL_DIR" && -d "$GLOBAL_DIR" ]]; then
        printf '%s\n' "$GLOBAL_DIR"
    else
        printf '%s\n' "$HOME"
    fi
}

macos_choose_path() {
    local selection_mode="$1"
    local prompt="$2"
    local start_dir="$3"

    if ! command -v osascript >/dev/null 2>&1; then
        echo "osascript is required for file selection on macOS." >&2
        return 2
    fi

    osascript -l JavaScript - "$selection_mode" "$prompt" "$start_dir" <<'JAVASCRIPT'
function run(argv) {
    var selectionMode = argv[0];
    var promptText = argv[1];
    var startDirectory = argv[2];
    var app = Application.currentApplication();
    app.includeStandardAdditions = true;

    var options = {withPrompt: promptText};
    if (startDirectory) {
        options.defaultLocation = Path(startDirectory);
    }

    try {
        if (selectionMode === "directory") {
            return app.chooseFolder(options).toString();
        }

        if (selectionMode === "multiple") {
            options.multipleSelectionsAllowed = true;
            var chosenFiles = app.chooseFile(options);
            var selectedPaths = [];
            for (var i = 0; i < chosenFiles.length; i++) {
                selectedPaths.push(chosenFiles[i].toString());
            }
            return selectedPaths.join("|");
        }

        return app.chooseFile(options).toString();
    } catch (error) {
        var errorText = String(error);
        if (error.errorNumber === -128 || error.number === -128 || errorText.indexOf("-128") !== -1) {
            return "";
        }
        throw error;
    }
}
JAVASCRIPT
}

macos_choose_directory() {
    local prompt="$1"
    local start_dir="$2"
    local selection

    selection=$(macos_choose_path "directory" "$prompt" "$start_dir") || return 2

    if [[ "$selection" != "/" ]]; then
        selection=${selection%/}
    fi
    printf '%s\n' "$selection"
}

macos_choose_file() {
    local prompt="$1"
    local start_dir="$2"

    macos_choose_path "file" "$prompt" "$start_dir" || return 2
}

macos_choose_multiple_files() {
    local prompt="$1"
    local start_dir="$2"

    macos_choose_path "multiple" "$prompt" "$start_dir" || return 2
}

select_directory_path() {
    local prompt="$1"
    local default_value="$2"
    local start_dir
    local selection
    local status

    start_dir=$(dialog_start_directory "$default_value")
    if [[ $(uname -s) == "Darwin" ]]; then
        macos_choose_directory "$prompt" "$start_dir"
        return $?
    fi

    if ! command -v zenity >/dev/null 2>&1; then
        echo "zenity is required for file selection on this platform." >&2
        return 2
    fi

    selection=$(zenity --file-selection --directory --title="$prompt" --height=500 --filename="${start_dir%/}/" 2>/dev/null)
    status=$?
    if [[ $status -eq 0 ]]; then
        printf '%s\n' "$selection"
        return 0
    elif [[ $status -eq 1 ]]; then
        return 0
    fi
    return 2
}

select_file_path() {
    local prompt="$1"
    local default_value="$2"
    local start_dir
    local selection
    local status

    start_dir=$(dialog_start_directory "$default_value")
    if [[ $(uname -s) == "Darwin" ]]; then
        macos_choose_file "$prompt" "$start_dir"
        return $?
    fi

    if ! command -v zenity >/dev/null 2>&1; then
        echo "zenity is required for file selection on this platform." >&2
        return 2
    fi

    selection=$(zenity --file-selection --title="$prompt" --filename="${start_dir%/}/" 2>/dev/null)
    status=$?
    if [[ $status -eq 0 ]]; then
        printf '%s\n' "$selection"
        return 0
    elif [[ $status -eq 1 ]]; then
        return 0
    fi
    return 2
}

select_multiple_file_paths() {
    local prompt="$1"
    local default_value="$2"
    local start_dir
    local selection
    local status

    start_dir=$(dialog_start_directory "$default_value")
    if [[ $(uname -s) == "Darwin" ]]; then
        macos_choose_multiple_files "$prompt" "$start_dir"
        return $?
    fi

    if ! command -v zenity >/dev/null 2>&1; then
        echo "zenity is required for file selection on this platform." >&2
        return 2
    fi

    selection=$(zenity --file-selection --multiple --separator="|" --title="$prompt" --filename="${start_dir%/}/" 2>/dev/null)
    status=$?
    if [[ $status -eq 0 ]]; then
        printf '%s\n' "$selection"
        return 0
    elif [[ $status -eq 1 ]]; then
        return 0
    fi
    return 2
}

# Function to prompt for a directory path with a platform-native graphical interface
prompt_for_directory() {
    local varname=$1
    local prompt=$2
    local default_value=$3
    local value

    echo "$prompt"

    if ! value=$(select_directory_path "$prompt" "$default_value"); then
        echo "Unable to open the directory selection dialog. Aborting." >&2
        exit 1
    fi

    # Check if user canceled the selection
    if [[ -z "$value" ]]; then
        echo "No directory selected. Aborting."
        exit 1
    fi

    config_values[$varname]="$value"
}

# Function to prompt for a file path with a platform-native graphical interface
prompt_for_file() {
    local varname=$1
    local prompt=$2
    local default_value=$3
    local value

    echo "$prompt"

    if ! value=$(select_file_path "$prompt" "$default_value"); then
        echo "Unable to open the file selection dialog. Aborting." >&2
        exit 1
    fi

    if [[ -z "$value" ]]; then
        echo "No file selected. Aborting."
        exit 1
    fi

    config_values[$varname]="$value"
}

# Function to prompt for an optional single file. Cancellation stores an empty value.
prompt_for_optional_file() {
    local varname=$1
    local prompt=$2
    local default_value=$3
    local value

    echo "$prompt"

    if ! value=$(select_file_path "$prompt" "$default_value"); then
        echo "Unable to open the file selection dialog. Aborting." >&2
        exit 1
    fi

    config_values[$varname]="$value"
}

# Function to prompt for a string value using terminal input
prompt_for_value() {
    local varname=$1
    local prompt=$2
    local default_value=$3

    # Prompt user in terminal
    read -p "$prompt [$default_value]:  " value

    value=$(trim_whitespace "$value")
    if [[ -z "$value" ]]; then
        value=$default_value
    fi

    config_values[$varname]="$value"
}

# Function to prompt for one or more masks (multi-selection)
prompt_for_masks() {
    local varname=$1
    local prompt=$2
    local default_dir=$3

    echo "$prompt"

    local selection
    if ! selection=$(select_multiple_file_paths "$prompt" "$default_dir"); then
        echo "Unable to open the mask selection dialog. Aborting." >&2
        exit 1
    fi

    config_values[$varname]="$selection"
}

# Function to display summary in the terminal and confirm
display_summary_and_confirm() {
    local summary="Configuration summary:\n"

    for var in "${!config_values[@]}"; do
        summary+="$var = ${config_values[$var]}\n"
    done

    # Display the summary in the terminal
    echo -e "$summary"

    # Ask for confirmation in terminal
    read -p "Do you want to save this configuration? (yes/no):  " confirm
    if [[ "$confirm" == "yes" || "$confirm" == "y" ]]; then
        return 0
    else
        return 1
    fi
}

# Function to write the config file
write_config_file() {

    local type=$1

    echo "# Configuration file generated by create_config.sh" >config_${type}.sh

    local value
    for var in "${!config_values[@]}"; do
        value="${config_values[$var]}"
        value="${value//\\/\\\\}"
        value="${value//\"/\\\"}"
        value="${value//\$/\\\$}"
        value="${value//\`/\\\`}"
        printf '%s="%s"\n' "$var" "$value" >>config_${type}.sh
    done
}

load_global_config() {
    local config_path="$1"
    local required_variables=(GLOBAL_DIR COHORT)
    local variable_name

    if [[ -z "$config_path" || ! -f "$config_path" ]]; then
        echo "Global configuration file not found: ${config_path:-<empty path>}" >&2
        return 1
    fi

    # Clear values left by any previously inspected cohort before loading the active one.
    unset GLOBAL_DIR COHORT T1_MNI DIR_RANALYSES LIST_ALL_SUBJECTS
    if ! source "$config_path"; then
        echo "Unable to load global configuration: $config_path" >&2
        return 1
    fi

    for variable_name in "${required_variables[@]}"; do
        if [[ -z "${!variable_name}" ]]; then
            echo "Global configuration '$config_path' is missing $variable_name." >&2
            return 1
        fi
    done

    if [[ ! -d "$GLOBAL_DIR" ]]; then
        echo "Configured GLOBAL_DIR is not an accessible directory: $GLOBAL_DIR" >&2
        return 1
    fi
    if [[ -n "${T1_MNI:-}" && ! -f "$T1_MNI" ]]; then
        echo "Configured T1_MNI file is unavailable: $T1_MNI" >&2
        return 1
    fi
    if [[ -n "${DIR_RANALYSES:-}" && ! -d "$DIR_RANALYSES" ]]; then
        echo "Configured DIR_RANALYSES is not an accessible directory: $DIR_RANALYSES" >&2
        return 1
    fi
    if [[ -n "${LIST_ALL_SUBJECTS:-}" && ( ! -f "$LIST_ALL_SUBJECTS" || ! -r "$LIST_ALL_SUBJECTS" ) ]]; then
        echo "Configured subject list is unavailable or unreadable: $LIST_ALL_SUBJECTS" >&2
        return 1
    fi

    return 0
}

#Function to find files in subject directory

##############################################################
################### GLOBAL ################################
##############################################################
# Main function to collect inputs and display summary
collect_and_confirm_inputs_global() {
    while true; do
        # Collect graphical directory/file inputs and terminal text inputs
        config_values[GLOBAL_DIR]=${GLOBAL_DIR}
        prompt_for_value "COHORT" "What is the name of the cohort ?" ""
        prompt_for_file "T1_MNI" "Select the standard space T1 that will be used to display results"
        if [[ ${config_values[T1_MNI]} == *.gz ]]; then
            gunzip -f ${config_values[T1_MNI]}
            config_values[T1_MNI]="${config_values[T1_MNI]%.gz}"
        fi

        prompt_for_directory "DIR_RANALYSES" "Where should report data and plots be exported?" ""
        prompt_for_file "LIST_ALL_SUBJECTS" "Select the .txt file with the list of ALL the subjects you have in your cohort (SUBJECT_VISIT)" "${config_values[GLOBAL_DIR]}"

        echo "Here is a summary of the informations for the GLOBAL configuration for VLAD in this cohort"
        # Display the summary in the terminal and prompt for confirmation
        if display_summary_and_confirm; then
            write_config_file global
            echo "Configuration file 'config_global.sh' has been created."
            break
        else
            echo "Configuration process will restart."
        fi
    done
}

create_new_config() {
    declare -A config_values

    echo "First, let's select the global directory from which all the files and results will be stored FOR THIS COHORT"

    if ! GLOBAL_DIR=$(select_directory_path "Global dir" "$HOME"); then
        echo "Unable to open the global directory selection dialog. Aborting." >&2
        exit 1
    fi

    # Check if user canceled the selection
    if [[ -z "$GLOBAL_DIR" ]]; then
        echo "No directory selected. Aborting."
        exit 1
    fi

    cd "${GLOBAL_DIR}" || exit 1
    echo "we are here $(pwd)"
    # Check if config file exists and prompt in the terminal for overwriting
    if [[ -f config_global.sh ]]; then
        read -p "config_global.sh already exists. Do you want to overwrite it or use it ? (overwrite / use):  " overwrite
        if [[ "$overwrite" == "overwrite" ]]; then
            rm config_global.sh
            # Start the input collection and confirmation process
            collect_and_confirm_inputs_global
        else
            echo "Let's go to specific configuration then"
        fi
    else

        # Start the input collection and confirmation process
        collect_and_confirm_inputs_global
        # if [[ $? -ne 0 ]]; then
        #     echo "There has been an error creating the config_global.sh, it will not be added to the configuration of VLAD"
        # else
        #     cd ${script_dir}
        #     echo "we are here $(pwd)"
        #     echo "${GLOBAL_DIR}/config_global.sh" >>VLAD_configs.txt
        #     source config_global.sh
        # fi
    fi

    #Making directories
    cd "${GLOBAL_DIR}" || exit 1
    mkdir -p Cartes4D
    mkdir -p Masques
    mkdir -p Results
    mkdir -p Scripts
    mkdir -p Listes
}

##############################################################
###################   MRSI    ################################
##############################################################
 
# Persist arrays as sourceable Bash values, including spaces and shell characters.
write_config_array() {
    local config_path="$1" array_name="$2"
    shift 2
    printf '%s=(' "$array_name" >>"$config_path"
    if (( $# > 0 )); then
        printf ' %q' "$@" >>"$config_path"
    fi
    printf ' )\n' >>"$config_path"
}

select_mrsi_analysis_description() {
    local description selection
    local candidates=()
    for description in "${FILES_MRSI_MET[@]}"; do
        case "$description" in
            crlb | Qmask | QMask | qmask | qMask) continue ;;
        esac
        candidates+=("$description")
    done
    if (( ${#candidates[@]} == 0 )); then
        echo "Enter at least one concentration description in the metabolite file list." >&2
        return 1
    fi
    if (( ${#candidates[@]} == 1 )); then
        config_values[MRSI_ANALYSIS_DESCRIPTION]="${candidates[0]}"
        return 0
    fi
    while true; do
        printf 'Available concentration descriptions: %s\n' "${candidates[*]}"
        read -r -p "Which description should VLAD analyze? " selection || return 1
        for description in "${candidates[@]}"; do
            if [[ "$selection" == "$description" ]]; then
                config_values[MRSI_ANALYSIS_DESCRIPTION]="$selection"
                return 0
            fi
        done
        echo "Please enter an exact description from the list."
    done
}

# Main function to collect inputs and display summary
collect_and_confirm_inputs_mrsi() {
    while true; do
        # Collect graphical directory/file inputs and terminal text inputs
        prompt_for_directory "NII_DIR_MRSI" "Select the path to subject MRSI files" "${GLOBAL_DIR}"
        # while true; do
        #     prompt_for_value "NII_DIR_MRSI_orga" "Are all the files in one directory (type : \"allinone\") or in a subject directory (type : \"bysubj\")" "bysubj"
        #     if [[ ${config_values[NII_DIR_MRSI_orga]} == "allinone" || ${config_values[NII_DIR_MRSI_orga]} == "bysubj" ]]; then
        #         break
        #     else
        #         echo "You didn't type a valid value. Let's start again"
        #     fi
        # done

        while true; do
            prompt_for_value "NII_DIR_MRSI_compression" "Are the nii files compressed )? Type yes/y or no/n" "yes"
            if [[ ${config_values[NII_DIR_MRSI_compression]} == "yes" || ${config_values[NII_DIR_MRSI_compression]} == "y" ]]; then
                MRSI_COMPRESSED=".gz"
                break
            elif [[ ${config_values[NII_DIR_MRSI_compression]} == "no" || ${config_values[NII_DIR_MRSI_compression]} == "n" ]]; then
                MRSI_COMPRESSED=""
                break
            else
                echo "You didn't type a valid value. Let's start again"
            fi
        done

        prompt_for_value "MRSI_SPACE_LABEL" "What is the value after space- in your file names for MRSI data ?" "mni"
        prompt_for_value "MRSI_EXTRA_TAGS" "List additional BIDS tags (ex: res-3.0mm) to insert after space (separate with spaces, leave blank for none)" ""

        prompt_for_masks "MRSI_MASK_LIST" "Select the masks to prepare for MRSI (use Ctrl/Cmd for multiple, Cancel to skip)" "${GLOBAL_DIR}/Masques"

        read -p "By default, the metabolites that will be processed are : CrPCr NAA NAAG GPCPCh Ins Glu Gln GABA GSH. If you want to change them, please answer yes. Any other answer will continue. Answer : " input
        if [[ ${input} == "yes" || ${input} == "y" ]]; then
            read -p "Please type ALL the metabolites, including the ones by default if you want them, that will be present in your files. Separate each one by space. Answer : " newmetabs
            METAB=(${newmetabs})
        fi

        read -p "By default, the processed files specific to each metabolites are (after desc) : signal_filtbiharmonic_pvcorr, crlb. If you want to change this list, please answer yes. Any other answer will continue. Answer : " input
        if [[ ${input} == "yes" || ${input} == "y" ]]; then
            read -p "Please type ALL the files, including the ones by default if you want them, that will be present in your files. Separate each one by space. Careful, case senitive. Answer : " newfiles
            FILES_MRSI_MET=(${newfiles})
        fi

        read -p "By default, the processed files global to each subject (after desc) : fwhm, snr. If you want to change this list, please answer yes. Any other answer will continue. Answer : " input
        if [[ ${input} == "yes" || ${input} == "y" ]]; then
            read -p "Please type ALL the files, including the ones by default if you want them, that will be present in your files. Separate each one by space. Careful, case senitive. Answer : " newfiles
            FILES_MRSI_GLOBAL=(${newfiles})
        fi

        select_mrsi_analysis_description || return 1
        config_values[MRSI_COMPRESSED]="$MRSI_COMPRESSED"
        # Display the summary in the terminal and prompt for confirmation
        if display_summary_and_confirm; then
            write_config_file mrsi
            write_config_array config_mrsi.sh METAB "${METAB[@]}"
            write_config_array config_mrsi.sh FILES_MRSI_MET "${FILES_MRSI_MET[@]}"
            write_config_array config_mrsi.sh FILES_MRSI_GLOBAL "${FILES_MRSI_GLOBAL[@]}"
            echo "Configuration file 'config_mrsi.sh' has been created."
            break
        else
            echo "Configuration process will restart."
        fi
    done
}

# Function to find files all in one directory
## GLOBAL MUST HAVE BEEN RUN BEFORE

find_files_mrsi() {

    local input_dir=$1
    local data_type=$2
    local acquisition=$3
    local compressed=$4

    # Variable to register modality if all files are found
    modality_found=true

    local spec=false global_description
    for global_description in "${FILES_MRSI_GLOBAL[@]}"; do
        if [[ "$acquisition" == "$global_description" ]]; then
            spec=true
            break
        fi
    done
    SUBJECTS=($(cat $LIST_ALL_SUBJECTS))
    # Loop through each subject and check if the file exists
    for subject in "${SUBJECTS[@]}"; do
        sub=${subject%_*}
        visit=${subject#*_}
        #echo "$spec"
        # if [[ ${config_values[NII_DIR_MRSI_orga]} == "allinone" ]]; then
        #     cd ${input_dir}
        # elif [[ ${config_values[NII_DIR_MRSI_orga]} == "bysubj" ]]; then
        #     cd ${input_dir}/${subject}
        # fi
        if [[ ${spec} == false ]]; then
            for MET in ${METAB[@]}; do
                #echo "$sub $visit $data_type $MET $acquisition $compressed"

                file_path=$(name_subject_file_mrsi "${sub}" "${visit}" "${data_type}" "${MET}" "${acquisition}" "${compressed}")

                #file_subject=$(name_subject_file "${sub}" "${visit}" "${data_type}" "${acquisition}" "${cardtodo}" "${COMPRESSION}")
                if [[ $? -ne 0 ]]; then
                    #echo "An error occurred while running name_subject_file_mrsi. Exiting script."
                    exit 1 # Exit script if the function returns an error
                fi

                cd ${input_dir}
                FILE_PATH=""
                FILE_PATH=$(find "${input_dir}" -type f -name "$file_path" 2>/dev/null | head -n 1)
                if [[ -z "$FILE_PATH" ]]; then
                    echo "File not found: $file_path" >>${GLOBAL_DIR}/not_found.txt
                    modality_found=false
                    break
                fi

                if ! check_file_not_empty "$FILE_PATH"; then
                    echo "File empty: $FILE_PATH" >>${GLOBAL_DIR}/not_found.txt
                    modality_found=false
                    break
                fi

            done
        else
            file_path=$(name_subject_file_mrsi_spec "${sub}" "${visit}" "${data_type}" "${acquisition}" "${compressed}")

            #file_subject=$(name_subject_file "${sub}" "${visit}" "${data_type}" "${acquisition}" "${cardtodo}" "${COMPRESSION}")
            if [[ $? -ne 0 ]]; then
                echo "An error occurred while running name_subject_file_mrsi_spec. Exiting script."
                exit 1 # Exit script if the function returns an error
            fi

            cd ${input_dir}
            FILE_PATH=""
            FILE_PATH=$(find "${input_dir}" -type f -name "$file_path" 2>/dev/null | head -n 1)
            if [[ -z "$FILE_PATH" ]]; then
                echo "File not found: $file_path" >>${GLOBAL_DIR}/not_found.txt
                modality_found=false
                break
            fi

            if ! check_file_not_empty "$FILE_PATH"; then
                echo "File empty: $FILE_PATH" >>${GLOBAL_DIR}/not_found.txt
                modality_found=false
                break
            fi

        fi
    done

    echo "$modality_found"
}

# Resolve quality inputs without changing where derived maps are written.
resolve_mrsi_quality_input() {
    local filename="$1" resolved
    if [[ -f "$PWD/$filename" ]]; then
        printf '%s\n' "$PWD/$filename"
        return 0
    fi
    resolved=$(find "${NII_DIR_MRSI}" -type f -name "$filename" -print -quit 2>/dev/null)
    if [[ -n "$resolved" ]]; then
        printf '%s\n' "$resolved"
        return 0
    fi
    echo "Quality input '$filename' not found in '$PWD' or under '$NII_DIR_MRSI'." >&2
    return 1
}

process_mrsi_nii() {

    local file=$1
    local modes=()
    if [[ "${B0_MODE}" == "7" ]]; then
        modes=("7" "7as3")
    else
        modes=("3")
    fi

    local active_metabs=()
    local sum_label=""
    local ratio_suffix=""
    local qmask_skip=()

    set_mode_vars() {
        local mode="$1"
        qmask_skip=()
        case "$mode" in
        7)
            active_metabs=("${METABSUM7T9[@]}")
            sum_label="SumMetabs9"
            ratio_suffix="RatioSum9"
            qmask_skip=("NAAG" "Gln")
            ;;
        7as3)
            active_metabs=("${METABSUM3T[@]}")
            sum_label="SumMetabs5"
            ratio_suffix="RatioSum5"
            ;;
        3 | *)
            active_metabs=("${METABSUM3T[@]}")
            sum_label="SumMetabs"
            ratio_suffix="RatioSum"
            ;;
        esac
    }

    set_mode_vars "${modes[0]}"
    METAB=("${active_metabs[@]}")
    for m in "${METAB[@]}"; do
        append_unique METAB_EXPORT "$m"
    done

    cd "${GLOBAL_DIR}" || exit 1
    if [[ -f config_global.sh ]]; then
        source config_global.sh
    else
        echo "There is an error, no global file !"
    fi

    cd ${NII_DIR_MRSI}
    SUBJECTS=($(cat $LIST_ALL_SUBJECTS))
    for SUBJ in ${SUBJECTS[@]}; do
        echo "=====Doing $SUBJ ======"
        sub=${SUBJ%_*}
        visit=${SUBJ#*_}
        cd ${NII_DIR_MRSI}

        file_name=$(name_subject_file_mrsi "${sub}" "${visit}" mrsi "${METAB[0]}" "${file}" "${MRSI_COMPRESSED}")
        if [[ $? -ne 0 ]]; then
            echo "An error occurred while running name_subject_file. Exiting script."
            exit 1 # Exit script if the function returns an error
        fi
        FILE_PATH=""
        FILE_PATH=$(find "${NII_DIR_MRSI}" -type f -name "$file_name" 2>/dev/null | head -n 1)
        echo $FILE_PATH
        if [[ -z "$FILE_PATH" ]]; then
            echo "File not found for ${file_name}, skipping subject ${SUBJ}"
            continue
        fi

        directory_towork=$(dirname "$FILE_PATH")

        cd "$directory_towork" || return 1

        for MODE in "${modes[@]}"; do
            set_mode_vars "$MODE"
            METAB=("${active_metabs[@]}")
            for m in "${METAB[@]}"; do
                append_unique METAB_EXPORT "$m"
            done

            local ratio_targets=("${active_metabs[@]}")
            local product_metabs=()
            for MET in "${active_metabs[@]}"; do
                skip=false
                for bad in "${qmask_skip[@]}"; do
                    if [[ "$MET" == "$bad" ]]; then
                        skip=true
                        break
                    fi
                done
                if [[ "$skip" == false ]]; then
                    product_metabs+=("$MET")
                fi
            done

            echo "Mode ${MODE}: Doing Sum of metabolites"
            if [[ ${file} == "crlb" || ${file} == "CRLB" ]]; then
                echo "CRLB file, skipping"
                continue
            fi

            METABSUM_ACTIVE=()
            METABQUOTIENTS=()
            COMMAND_SUM=()
            first_command="$(name_subject_file_mrsi "${sub}" "${visit}" "mrsi" "${active_metabs[0]}" "${file}" "${MRSI_COMPRESSED}")"

            COMMAND_SUM=("$first_command")
            for ((i = 1; i < ${#active_metabs[@]}; i++)); do
                add_command="-add $(name_subject_file_mrsi $sub $visit mrsi "${active_metabs[i]}" $file "${MRSI_COMPRESSED}")"
                COMMAND_SUM+=("$add_command")
            done
            sum_command="$(name_subject_file_mrsi $sub $visit mrsi "${sum_label}" $file "${MRSI_COMPRESSED}")"
            COMMAND_SUM+=("$sum_command")

            sum_ok=false
            if should_create_file "${sum_command}"; then
                if fslmaths ${COMMAND_SUM[@]}; then
                    sum_ok=true
                else
                    PREP_FAILED=true
                    echo "Error for ${sum_label} for $SUBJ"
                fi
            else
                sum_ok=true
            fi

            if [[ "${sum_ok}" == true ]]; then
                echo "Mode ${MODE}: Doing RatioSum"
                for MET in "${ratio_targets[@]}"; do
                    echo "RatioSum of $MET"
                    ratio_out=$(name_subject_file_mrsi "${sub}" "${visit}" "mrsi" "${MET}${ratio_suffix}" "${file}" "${MRSI_COMPRESSED}")
                    status=0
                    if should_create_file "$ratio_out"; then
                        fslmaths $(name_subject_file_mrsi "${sub}" "${visit}" "mrsi" "${MET}" "${file}" "${MRSI_COMPRESSED}") -div $(name_subject_file_mrsi "${sub}" "${visit}" "mrsi" "${sum_label}" "${file}" "${MRSI_COMPRESSED}") "$ratio_out"
                        status=$?
                    fi
                    if [[ $status -eq 0 ]]; then
                        METABSUM_ACTIVE+=("${MET}${ratio_suffix}")
                        append_unique METABSUM_EXPORT "${MET}${ratio_suffix}"
                    else
                        PREP_FAILED=true
                        echo "Error for RatioSum of $MET for $SUBJ"
                    fi
                done
                METABSUM_ACTIVE+=("${sum_label}")
                append_unique METABSUM_EXPORT "${sum_label}"
            else
                echo "Skipping RatioSum for $SUBJ because ${sum_label} failed."
            fi

            echo "Mode ${MODE}: Doing Quotients"
            for ((i = 0; i < ${#METAB[@]}; i++)); do
                for ((j = i + 1; j < ${#METAB[@]}; j++)); do
                    numerator=${METAB[i]}
                    denominator=${METAB[j]}
                    if [[ ${METAB[i]} == "CrPCr" || ${METAB[j]} == "CrPCr" ]]; then
                        numerator=$([[ ${METAB[i]} == "CrPCr" ]] && echo "${METAB[j]}" || echo "${METAB[i]}")
                        denominator="CrPCr"
                    fi

                    echo "Dividing ${numerator} by ${denominator}"
                    METABQUOTIENT="${numerator}on${denominator}"

                    quotient_out=$(name_subject_file_mrsi $sub $visit mrsi "${METABQUOTIENT}" $file "${MRSI_COMPRESSED}")
                    status=0
                    if should_create_file "$quotient_out"; then
                        fslmaths $(name_subject_file_mrsi $sub $visit mrsi "${numerator}" $file "${MRSI_COMPRESSED}") -div $(name_subject_file_mrsi $sub $visit mrsi "${denominator}" $file "${MRSI_COMPRESSED}") "$quotient_out"
                        status=$?
                    fi
                    if [[ $status -eq 0 ]]; then
                        METABQUOTIENTS+=("${METABQUOTIENT}")
                        append_unique METABQUOTIENTS_EXPORT "${METABQUOTIENT}"
                    else
                        echo "Error for Metabquotient of ${numerator} on ${denominator} for $SUBJ"
                    fi
                done
            done

            echo "Mode ${MODE}: Doing QMASKS"
            SNR_Thresh=4
            FWHM_Thresh=0.1
            CRLB_Thresh=20
            local snr_input fwhm_input crlb_input quality_filename qmask_failed
            quality_filename=$(name_subject_file_mrsi_spec "$sub" "$visit" mrsi snr "$MRSI_COMPRESSED")
            if ! snr_input=$(resolve_mrsi_quality_input "$quality_filename"); then
                echo "Skipping QMASKS for $SUBJ, mode $MODE: missing SNR input." >&2
                continue
            fi
            quality_filename=$(name_subject_file_mrsi_spec "$sub" "$visit" mrsi fwhm "$MRSI_COMPRESSED")
            if ! fwhm_input=$(resolve_mrsi_quality_input "$quality_filename"); then
                echo "Skipping QMASKS for $SUBJ, mode $MODE: missing FWHM input." >&2
                continue
            fi
            if ! fslmaths "$snr_input" -nan -thr "$SNR_Thresh" -bin "QMask_SNR_${sub}_${visit}" ||
               ! fslmaths "$fwhm_input" -nan -uthr "$FWHM_Thresh" -bin "QMask_FWHM_${sub}_${visit}"; then
                echo "Skipping QMASKS for $SUBJ, mode $MODE: quality thresholding failed." >&2
                continue
            fi

            qmask_failed=false
            for MET in "${METAB[@]}"; do
                qmask_out=$(name_subject_file_mrsi "$sub" "$visit" mrsi "$MET" QMask "$MRSI_COMPRESSED")
                if should_create_file "$qmask_out"; then
                    quality_filename=$(name_subject_file_mrsi "$sub" "$visit" mrsi "$MET" crlb "$MRSI_COMPRESSED")
                    if ! crlb_input=$(resolve_mrsi_quality_input "$quality_filename"); then
                        echo "Skipping QMask for $MET, $SUBJ: missing CRLB input." >&2
                        qmask_failed=true
                        continue
                    fi
                    if ! fslmaths "$crlb_input" -nan -uthr "$CRLB_Thresh" -bin "QMask_CRLB_${MET}_${sub}_${visit}" ||
                       ! fslmaths "QMask_SNR_${sub}_${visit}" -mul "QMask_FWHM_${sub}_${visit}" -mul "QMask_CRLB_${MET}_${sub}_${visit}" "$qmask_out"; then
                        echo "QMask creation failed for $MET, $SUBJ." >&2
                        qmask_failed=true
                    fi
                fi
            done
            if [[ "$qmask_failed" == true ]]; then
                echo "Skipping combined QMask for $SUBJ, mode $MODE: a metabolite QMask failed." >&2
                continue
            fi

            if [[ ${#product_metabs[@]} -gt 0 ]]; then
                COMMAND_SUM_MASK=()
                first_command="$(name_subject_file_mrsi "${sub}" "${visit}" "mrsi" "${product_metabs[0]}" "QMask" "${MRSI_COMPRESSED}")"

                COMMAND_SUM_MASK=("$first_command")
                for ((i = 1; i < ${#product_metabs[@]}; i++)); do
                    add_command="-mul $(name_subject_file_mrsi $sub $visit mrsi "${product_metabs[i]}" QMask "${MRSI_COMPRESSED}")"
                    COMMAND_SUM_MASK+=("$add_command")

                done
                sum_command="$(name_subject_file_mrsi $sub $visit mrsi "${sum_label}" QMask "${MRSI_COMPRESSED}")"
                COMMAND_SUM_MASK+=("$sum_command")
                if should_create_file "$sum_command"; then
                    fslmaths ${COMMAND_SUM_MASK[@]}
                fi
            fi
        done
    done

}

mrsi_do() {
    declare -A config_values
    cd "$GLOBAL_DIR" || exit 1

    if [[ "$NONINTERACTIVE" == false ]]; then
        # Check if config file exists and prompt in the terminal for overwriting
        if [[ -f config_mrsi.sh ]]; then
            read -p "config_mrsi.sh already exists. Do you want to overwrite it? (yes/no):  " overwrite
            if [[ "$overwrite" != "yes" && "$overwrite" != "y" ]]; then
                echo "Aborting."
                exit 1
            fi
        fi

        collect_and_confirm_inputs_mrsi
        if [[ $? -ne 0 ]]; then
            echo "There has been an error generating the configuration file"
            break
        fi

        cd "${GLOBAL_DIR}" || exit 1
    fi

    source config_mrsi.sh

    case "${B0_MODE}" in
    7)
        METAB=("${METABSUM7T9[@]}" "${METABSUM3T[@]}")
        ;;
    *)
        METAB=("${METABSUM3T[@]}")
        ;;
    esac

    MRSI_FILES_VALIDATED=()
    for file_mrsi in ${FILES_MRSI_MET[@]}; do
        echo "Checking $file_mrsi"
        check_for_file=$(find_files_mrsi "${NII_DIR_MRSI}" mrsi "${file_mrsi}" "${MRSI_COMPRESSED}")
        echo "check_for_file : $check_for_file"
        if [[ "$(echo -n "${check_for_file}")" == "false" ]]; then
            echo "$file_mrsi is missing some subjects. There will be no validation for this file"
            [[ "$NONINTERACTIVE" == false ]] || { echo "Preparation stopped: required inputs are missing; inspect ${GLOBAL_DIR}/not_found.txt." >&2; exit 1; }
            read -p "Files are missing : Do you want to continue ? You might experienced some bugs. Check the file not_found.txt in your main directory to see which subjects bugged. Continue ? :" continue
            if [[ $continue == "y" || $continue == "yes" ]]; then
                echo "ok !"
            else
                echo "See you soon"
                exit 1
            fi

        elif [[ "$(echo -n "${check_for_file}")" == "true" ]]; then
            echo "All good, all the files $file_mrsi are where we are expecting them and are validated ! :-)"
            MRSI_FILES_VALIDATED+=("$file_mrsi")
        else
            echo "We have a bug, aborting"
            exit 1

        fi
    done

    MRSI_GLOBAL_FILES_VALIDATED=()
    for file_mrsi in "${FILES_MRSI_GLOBAL[@]}"; do
        check_for_file=$(find_files_mrsi "${NII_DIR_MRSI}" mrsi "${file_mrsi}" "${MRSI_COMPRESSED}")
        if [[ "$(echo -n "${check_for_file}")" == "false" ]]; then
            echo "$file_mrsi is missing some subjects. There will be no validation for this file"
            [[ "$NONINTERACTIVE" == false ]] || { echo "Preparation stopped: required inputs are missing; inspect ${GLOBAL_DIR}/not_found.txt." >&2; exit 1; }
            read -p "Files are missing : Do you want to continue ? You might experienced some bugs. Check the file not_found.txt in your main directory to see which subjects bugged. Continue ? :" continue
            if [[ $continue == "y" || $continue == "yes" ]]; then
                echo "ok !"
            else
                echo "See you soon"
                exit 1
            fi

        elif [[ "$(echo -n "${check_for_file}")" == "true" ]]; then
            echo "All good, all the files $file_mrsi are where we are expecting them and are validated ! :-)"
            MRSI_GLOBAL_FILES_VALIDATED+=("$file_mrsi")
        else
            echo "We have a bug, aborting"
            exit 1

        fi
    done

    cd "${GLOBAL_DIR}" || return 1
    write_config_array config_mrsi.sh MRSI_FILES_VALIDATED "${MRSI_FILES_VALIDATED[@]}"
    write_config_array config_mrsi.sh MRSI_GLOBAL_FILES_VALIDATED "${MRSI_GLOBAL_FILES_VALIDATED[@]}"

    local metric found description
    for metric in "$MRSI_ANALYSIS_DESCRIPTION" crlb snr fwhm; do
        found=false
        for description in "${MRSI_FILES_VALIDATED[@]}" "${MRSI_GLOBAL_FILES_VALIDATED[@]}"; do
            [[ "$metric" == "$description" ]] && found=true
        done
        if [[ "$found" == false ]]; then
            echo "MRSI preparation incomplete: '$metric' was not validated. Concentration analysis requires the selected description and quality metrics crlb, snr, fwhm. Correct the file lists or missing inputs and rerun Prep." >&2
            return 1
        fi
    done

    if [[ -n "${MRSI_MASK_LIST}" && ${#MRSI_FILES_VALIDATED[@]} -gt 0 ]]; then
        local reference_mrsi
        reference_mrsi=$(get_reference_image_path "mrsi" "${MRSI_FILES_VALIDATED[0]}")
        if [[ -n "$reference_mrsi" ]]; then
            if ! configure_masks_for_modality "mrsi" "${MRSI_MASK_LIST}" "$reference_mrsi"; then
                echo "MRSI mask preparation failed. Configuration was not completed." >&2
                return 1
            fi
        else
            echo "Unable to determine an MRSI reference image for mask validation."
        fi
    else
        echo "No MRSI mask list provided or no validated files. Skipping mask preparation for MRSI."
    fi

    for file_mrsi_val in ${MRSI_FILES_VALIDATED[@]}; do
        echo "======== Doing file $file_mrsi_val ======="
        process_mrsi_nii "${file_mrsi_val}"
    done

    cd "${GLOBAL_DIR}" || return 1
    write_config_array config_mrsi.sh METAB "${METAB_EXPORT[@]}"
    write_config_array config_mrsi.sh METABSUM "${METABSUM_EXPORT[@]}"
    write_config_array config_mrsi.sh METABQUOTIENTS "${METABQUOTIENTS_EXPORT[@]}"
}

##############################################################
################### DTI ################################
##############################################################

# Main function to collect inputs and display summary

collect_and_confirm_inputs_dti() {
    while true; do
        # Collect graphical directory/file inputs and terminal text inputs
        prompt_for_directory "NII_DIR_DTI" "Select the path to subject DTI files" "${GLOBAL_DIR}"
        # while true; do
        #     prompt_for_value "NII_DIR_DTI_orga" "Are all the files in one directory (type : \"allinone\") or in a subject directory (type : \"bysubj\")" "bysubj"
        #     if [[ ${config_values[NII_DIR_DTI_orga]} == "allinone" || ${config_values[NII_DIR_DTI_orga]} == "bysubj" ]]; then
        #         break
        #     else
        #         echo "You didn't type a valid value. Let's start again"
        #     fi
        # done

        while true; do
            prompt_for_value "NII_DIR_DTI_compression" "Are the nii files compressed )? Type yes/y or no/n" "yes"
            if [[ ${config_values[NII_DIR_DTI_compression]} == "yes" || ${config_values[NII_DIR_DTI_compression]} == "y" ]]; then
                DTI_COMPRESSED=".gz"
                break
            elif [[ ${config_values[NII_DIR_DTI_compression]} == "no" || ${config_values[NII_DIR_DTI_compression]} == "n" ]]; then
                DTI_COMPRESSED=""
            else
                echo "You didn't type a valid value. Let's start again"
            fi
        done

        prompt_for_value "DTI_BIDS_INFIX" "BIDS tags between sub-..._ses-... and desc- in your DTI filenames (ex: space-mni_acq-dti)" "space-mni_acq-dti"
        config_values[DTI_BIDS_INFIX]=$(normalize_bids_infix "${config_values[DTI_BIDS_INFIX]}")
        prompt_for_value "DTI_DATA_SUFFIX" "Suffix after desc-..._ in your DTI filenames (ex: dwi)" "dwi"

        echo "WARNING : gFA files are maybe named wierdly by me... Feel free to change the script in further use if needed"
        read -p "By default, the processed files that will be processed are : gFA. If you want to change this list, please answer yes. Any other answer will continue. Answer : " input
        if [[ ${input} == "yes" || ${input} == "y" ]]; then
            read -p "Please type ALL the files, including the ones by default if you want them, that will be present in your files. Separate each one by space. Careful, case senitive. Answer : " newfiles
            FILES_DTI=(${newfiles})
        fi

        prompt_for_optional_file "DTI_MASK_LIST" "Select the single primary DTI analysis mask (Cancel to skip)" "${GLOBAL_DIR}/Masques"

        # Display the summary in the terminal and prompt for confirmation
        if display_summary_and_confirm; then
            write_config_file dti

            echo "Configuration file 'config_dti.sh' has been created."
            break
        else
            echo "Configuration process will restart."
        fi
    done
}

find_files() {

    local input_dir=$1
    local data_type=$2
    local bids_infix=$3
    local description=$4
    local compressed=$5

    input_dir=$(trim_whitespace "$input_dir")
    data_type=$(trim_whitespace "$data_type")
    bids_infix=$(normalize_bids_infix "$bids_infix")
    description=$(trim_whitespace "$description")
    compressed=$(trim_whitespace "$compressed")

    SUBJECTS=($(cat $LIST_ALL_SUBJECTS))
    # Variable to register modality if all files are found
    modality_found=true

    # if [[ "$data_type" == "T1" || "$data_type" == "T1w" ]]; then
    #     local orga=${NII_DIR_STRUCTURAL_orga}
    # elif [[ "$data_type" == "DTI" || "$data_type" == "dti" || "$data_type" == "DWI" || "$data_type" == "dwi" ]]; then
    #     local orga=${NII_DIR_DTI_orga}
    # else
    #     echo "Data type not recognized, exiting because we can't check files"
    #     exit 1
    # fi

    if [[ ${#SUBJECTS[@]} -eq 0 ]]; then
        echo "Subjects array empty"
    else

        # Loop through each subject and check if the file exists
        for subject in "${SUBJECTS[@]}"; do
            sub=${subject%_*}
            visit=${subject#*_}

            # if [[ $orga == "allinone" ]]; then # A CHANGER SELON STRUCTURAL / DTI
            #     cd ${input_dir}
            # elif [[ $orga == "bysubj" ]]; then
            #     cd ${input_dir}/${subject}
            # fi

            file_path=$(name_subject_file "${sub}" "${visit}" "${data_type}" "${bids_infix}" "${description}" "${compressed}")
            #echo "$file_path"

            if [[ $? -ne 0 ]]; then
                echo "An error occurred while running name_subject_file. Exiting script."
                exit 1 # Exit script if the function returns an error
            fi

            cd ${input_dir}
            FILE_PATH=""
            FILE_PATH=$(find "${input_dir}" -type f -name "$file_path" 2>/dev/null | head -n 1)
            if [[ -z "$FILE_PATH" ]]; then
                echo "File not found: $file_path" >>${GLOBAL_DIR}/not_found.txt
                modality_found=false
                #break
            elif ! check_file_not_empty "$FILE_PATH"; then
                echo "File empty: $FILE_PATH" >>${GLOBAL_DIR}/not_found.txt
                modality_found=false
                #break
            fi

        done
    fi

    echo "$modality_found"
}

dti_do() {

    declare -A config_values
    cd "$GLOBAL_DIR" || exit 1

    if [[ "$NONINTERACTIVE" == false ]]; then
        # Check if config file exists and prompt in the terminal for overwriting
        if [[ -f config_dti.sh ]]; then
            read -p "config_dti.sh already exists. Do you want to overwrite it? (yes/no): " overwrite
            if [[ "$overwrite" != "yes" && "$overwrite" != "y" ]]; then
                echo "Aborting."
                exit 1
            fi
        fi

        collect_and_confirm_inputs_dti

        cd "$GLOBAL_DIR" || exit 1
    fi

    source config_dti.sh

    local dtisuffix=${DTI_DATA_SUFFIX:-dwi}
    local dtibids
    dtibids=$(resolve_bids_infix "${DTI_BIDS_INFIX:-}" "${DTI_SPACE_LABEL:-mni}" "${DTI_EXTRA_TAGS:-}" "${DTI_ACQ_LABEL:-dti}")

    DTI_FILES_VALIDATED=()
    for file_dti in ${FILES_DTI[@]}; do
        check_for_file=""
        not_found=()
        check_for_file=$(find_files "${NII_DIR_DTI}" "${dtisuffix}" "${dtibids}" "${file_dti}" "${DTI_COMPRESSED}")
        if [[ $check_for_file == "false" ]]; then
            echo "$file_dti is missing some subjects. Will not be registrated as validated file"
            echo "${not_found[@]}"
        elif [[ $check_for_file == "true" ]]; then
            echo "All good, all the files are where we are expecting them for $file_dti modality ! :-)"
            DTI_FILES_VALIDATED+=("$file_dti")
        else
            echo "We have a problem in the checking, do not trust these results"
        fi
    done

    echo "DTI_FILES_VALIDATED=(${DTI_FILES_VALIDATED[@]})" >>config_dti.sh

    if [[ -n "${DTI_MASK_LIST}" && ${#DTI_FILES_VALIDATED[@]} -gt 0 ]]; then
        parse_mask_entries "${DTI_MASK_LIST}"
        if [[ ${#PARSED_MASK_ENTRIES[@]} -ne 1 ]]; then
            echo "DTI requires exactly one primary analysis mask; ${#PARSED_MASK_ENTRIES[@]} were selected." >&2
            return 1
        fi

        local reference_dti
        reference_dti=$(get_reference_image_path "dti" "${DTI_FILES_VALIDATED[0]}")
        if [[ -n "$reference_dti" ]]; then
            if ! configure_masks_for_modality "dti" "${DTI_MASK_LIST}" "$reference_dti"; then
                echo "DTI mask preparation failed. Configuration was not completed." >&2
                return 1
            fi
            if [[ ${#PREPARED_MASK_PATHS[@]} -ne 1 ]]; then
                echo "DTI mask preparation did not produce exactly one primary mask." >&2
                return 1
            fi

            local dti_mask_filename
            dti_mask_filename=$(basename "${PREPARED_MASK_PATHS[0]}")
            printf 'DTI_ANALYSIS_MASK_FILENAME=%q\n' "$dti_mask_filename" >>config_dti.sh
            echo "Primary DTI analysis mask: $dti_mask_filename"
        else
            echo "Unable to determine a DTI reference image for mask validation." >&2
            return 1
        fi
    else
        echo "No DTI mask list provided or no validated files. Skipping mask preparation for DTI."
    fi
}

##############################################################
################### STRUCTURAL ################################
##############################################################

collect_and_confirm_inputs_structural() {
    while true; do
        # Collect graphical directory/file inputs and terminal text inputs
        prompt_for_directory "NII_DIR_STRUCTURAL" "Select the path to subject STRUCTURAL files" "${GLOBAL_DIR}"
        while true; do
            prompt_for_value "NII_DIR_STRUCTURAL_orga" "Are all the files in one directory (type : \"allinone\") or in a subject directory (type : \"bysubj\")" "bysubj"
            if [[ ${config_values[NII_DIR_STRUCTURAL_orga]} == "allinone" || ${config_values[NII_DIR_STRUCTURAL_orga]} == "bysubj" ]]; then
                break
            else
                echo "You didn't type a valid value. Let's start again"
            fi
        done

        while true; do
            prompt_for_value "NII_DIR_STRUCTURAL_compression" "Are the nii files compressed )? Type yes/y or no/n" "yes"
            if [[ ${config_values[NII_DIR_STRUCTURAL_compression]} == "yes" || ${config_values[NII_DIR_STRUCTURAL_compression]} == "y" ]]; then
                STRUCTURAL_COMPRESSED=".gz"
                break
            elif [[ ${config_values[NII_DIR_STRUCTURAL_compression]} == "no" || ${config_values[NII_DIR_STRUCTURAL_compression]} == "n" ]]; then
                STRUCTURAL_COMPRESSED=""
                break
            else
                echo "You didn't type a valid value. Let's start again"
            fi
        done

        prompt_for_value "STRUCTURAL_BIDS_INFIX" "BIDS tags between sub-..._ses-... and desc- in your STRUCTURAL filenames (ex: space-mni_acq-memprage)" "space-mni_acq-memprage"
        config_values[STRUCTURAL_BIDS_INFIX]=$(normalize_bids_infix "${config_values[STRUCTURAL_BIDS_INFIX]}")
        prompt_for_value "STRUCTURAL_DATA_SUFFIX" "Suffix after desc-..._ in your STRUCTURAL filenames (ex: T1w)" "T1w"

        read -p "By default, the processed files that will be processed are : mwp1, mwp2, wp1, wp2 (Following CAT12 nomenclature). If you want to change this list, please answer yes. Any other answer will continue. Answer : " input
        if [[ ${input} == "yes" || ${input} == "y" ]]; then
            read -p "Please type ALL the files, including the ones by default if you want them, that will be present in your files. Separate each one by space. Careful, case senitive. Answer : " newfiles
            FILES_STRUCTURAL=(${newfiles})
        fi

        prompt_for_masks "STRUCTURAL_MASK_LIST" "Select the masks to prepare for STRUCTURAL data (use Ctrl/Cmd for multiple, Cancel to skip)" "${GLOBAL_DIR}/Masques"

        # Display the summary in the terminal and prompt for confirmation
        if display_summary_and_confirm; then
            write_config_file structural

            echo "Configuration file 'config_structural.sh' has been created."
            break
        else
            echo "Configuration process will restart."
        fi
    done
}

structural_do() {

    declare -A config_values
    cd "$GLOBAL_DIR" || exit 1

    if [[ "$NONINTERACTIVE" == false ]]; then
        # Check if config file exists and prompt in the terminal for overwriting
        if [[ -f config_structural.sh ]]; then
            read -p "config_structural.sh already exists. Do you want to overwrite it? (yes/no):  " overwrite
            if [[ "$overwrite" != "yes" && "$overwrite" != "y" ]]; then
                echo "Aborting."
                exit 1
            fi
        fi

        collect_and_confirm_inputs_structural

        cd "$GLOBAL_DIR" || exit 1
    fi

    source config_structural.sh

    local structsuffix=${STRUCTURAL_DATA_SUFFIX:-T1w}
    local structbids
    structbids=$(resolve_bids_infix "${STRUCTURAL_BIDS_INFIX:-}" "${STRUCTURAL_SPACE_LABEL:-mni}" "${STRUCTURAL_EXTRA_TAGS:-}" "${STRUCTURAL_ACQ_LABEL:-memprage}")

    STRUCTURAL_FILES_VALIDATED=()
    for file_structural in ${FILES_STRUCTURAL[@]}; do
        check_for_file=""
        not_found=""
        check_for_file=$(find_files "${NII_DIR_STRUCTURAL}" "${structsuffix}" "${structbids}" "${file_structural}" "${STRUCTURAL_COMPRESSED}")
        #echo "$check_for_file"
        #declare -p check_for_file
        if [[ "$(echo -n "${check_for_file}")" == "false" ]]; then
            echo "$file_structural is missing some subjects. Files will not be validated"
            echo "${not_found[@]}"
        elif [[ "$(echo -n "${check_for_file}")" == "true" ]]; then
            echo "All good, all the files are where we are expecting them for $file_structural modality ! :-)"
            STRUCTURAL_FILES_VALIDATED+=("$file_structural")
        else
            echo "Problem, the variable is neither true or false..."

        fi
    done

    cd "${GLOBAL_DIR}" || exit 1
    echo "STRUCTURAL_FILES_VALIDATED=(${STRUCTURAL_FILES_VALIDATED[@]})" >>config_structural.sh


    if [[ -n "${STRUCTURAL_MASK_LIST}" && ${#STRUCTURAL_FILES_VALIDATED[@]} -gt 0 ]]; then
        local reference_struct
        reference_struct=$(get_reference_image_path "structural" "${STRUCTURAL_FILES_VALIDATED[0]}")
        if [[ -n "$reference_struct" ]]; then
            if ! configure_masks_for_modality "structural" "${STRUCTURAL_MASK_LIST}" "$reference_struct"; then
                echo "STRUCTURAL mask preparation failed. Configuration was not completed." >&2
                return 1
            fi
        else
            echo "Unable to determine a STRUCTURAL reference image for mask validation."
        fi
    else
        echo "No STRUCTURAL mask list provided or no validated files. Skipping mask preparation for STRUCTURAL."
    fi

    
}

##############################################################
################### LAUNCHING SCRIPT ################################
##############################################################

if [[ "$NONINTERACTIVE" == true ]]; then
    set -e
    [[ -f "$PREP_CONFIG" ]] || { echo "--non-interactive requires an existing --config file." >&2; exit 1; }
    case "$PREP_MODALITY" in mrsi|dti|structural) ;; *) echo "Choose --modality MRSI, DTI or STRUCTURAL." >&2; exit 1 ;; esac
    source "$PREP_CONFIG" || exit 1
    for required in GLOBAL_DIR COHORT T1_MNI LIST_ALL_SUBJECTS; do
        [[ -n "${!required}" ]] || { echo "Missing cohort setting: $required" >&2; exit 1; }
        [[ "${!required}" != *[[:space:]]* ]] || { echo "Legacy preparation needs whitespace-free study/input paths: $required." >&2; exit 1; }
    done
    [[ -f "$T1_MNI" && -s "$LIST_ALL_SUBJECTS" ]] || { echo "Reference image or subject list is missing/empty." >&2; exit 1; }
    cd "$GLOBAL_DIR" || exit 1
    [[ -f "config_${PREP_MODALITY}.sh" ]] || { echo "Save the selected modality settings before preparing data." >&2; exit 1; }
    source "config_${PREP_MODALITY}.sh" || exit 1
    upper_modality=$(printf '%s' "$PREP_MODALITY" | tr '[:lower:]' '[:upper:]')
    input_mode_setting="${upper_modality}_INPUT_MODE"
    if [[ "${!input_mode_setting:-legacy}" == bids ]]; then
        prep_args=(--config "$PREP_CONFIG" --modality "$upper_modality")
        [[ "$OVERWRITE" != true ]] || prep_args+=(--overwrite)
        [[ -z "$PREP_SUBJECTS" ]] || prep_args+=(--subjects "$PREP_SUBJECTS")
        "${VLAD_PYTHON_BIN}" -m vlad.preparation "${prep_args[@]}" || exit $?
        exit 0
    fi
    [[ -z "$PREP_SUBJECTS" ]] || { echo "--subjects is available only for BIDS derivative preparation." >&2; exit 1; }
    input_setting="NII_DIR_${upper_modality}"
    compression_setting="NII_DIR_${upper_modality}_compression"
    [[ -d "${!input_setting}" && "${!input_setting}" != *[[:space:]]* ]] || { echo "Input directory is missing or contains whitespace: ${!input_setting}" >&2; exit 1; }
    compressed=""
    [[ "${!compression_setting}" != yes && "${!compression_setting}" != y ]] || compressed=".gz"
    script_dir="$VLAD_HOME"
    PYTHON_HELPERS="${VLAD_HOME}/src/vlad/legacy"
    [[ -d "$PYTHON_HELPERS" ]] || PYTHON_HELPERS="${VLAD_HOME}/../legacy"
    vlad_config="$VLAD_CONFIG_FILE"
    mkdir -p Cartes4D Masques Results Scripts Listes
    case "$PREP_MODALITY" in
        mrsi)
            MRSI_COMPRESSED="$compressed"
            [[ ${#FILES_MRSI_MET[@]} -gt 0 ]] || FILES_MRSI_MET=(signal_filtbiharmonic_pvcorr crlb)
            [[ ${#FILES_MRSI_GLOBAL[@]} -gt 0 ]] || FILES_MRSI_GLOBAL=(fwhm snr)
            mrsi_do
            [[ ${#MRSI_FILES_VALIDATED[@]} -gt 0 ]] || { echo "No MRSI files passed validation." >&2; exit 1; }
            ;;
        dti)
            DTI_COMPRESSED="$compressed"
            [[ ${#FILES_DTI[@]} -gt 0 ]] || FILES_DTI=(gFA)
            dti_do
            [[ ${#DTI_FILES_VALIDATED[@]} -eq ${#FILES_DTI[@]} ]] || { echo "Some DTI files did not pass validation." >&2; exit 1; }
            ;;
        structural)
            STRUCTURAL_COMPRESSED="$compressed"
            [[ ${#FILES_STRUCTURAL[@]} -gt 0 ]] || FILES_STRUCTURAL=(mwp1 mwp2 wp1 wp2)
            structural_do
            [[ ${#STRUCTURAL_FILES_VALIDATED[@]} -eq ${#FILES_STRUCTURAL[@]} ]] || { echo "Some structural files did not pass validation." >&2; exit 1; }
            ;;
    esac
    [[ "$PREP_FAILED" == false ]] || { echo "One or more derived image operations failed." >&2; exit 1; }
    echo "Cohort data preparation completed."
    exit 0
fi

echo "================================="
echo "Hi ! Let's create and organize the files you need ! :-)"
echo "First, are you sure that your files are named correctly."
echo "Structure that we want : "
echo "sub: subject ; ses : session/visit, if only one put V1 ; desc : description of the file (more liberal ^^)"
echo "For DTI and structural files, VLAD will ask for the full BIDS tag block between sub-SUBJECT_ses-SESSION and desc-DESCRIPTION."
echo "Example 1 : sub-SUBJECT_ses-SESSION_space-MNI_acq-ACQUISITION_desc-gFA_dwi.nii.gz"
echo "Example 2 : sub-SUBJECT_ses-SESSION_space-MNI_acq-memprage_desc-mwp1_T1w.nii.gz"
read -p " If they are not in this structure, answer exit and use the examples provided to rename your files in the most standard way possible to be able to continue further. Anything else will continue.  " answer
if [[ $answer == "exit" ]]; then
    echo "Well good luck and see you soon here. Tip : pompt chatGPT or Claude with the current structure of your files and the structure you want to have, it will do the script(s) :-)"
    exit 0
else
    echo "Let's proceed to the rest"
fi

script_dir="$VLAD_HOME"
PYTHON_HELPERS="${VLAD_HOME}/src/vlad/legacy"
[[ -d "$PYTHON_HELPERS" ]] || PYTHON_HELPERS="${VLAD_HOME}/../legacy"

# Path to VLAD_configs.sh
vlad_config="$VLAD_CONFIG_FILE"
active_global_config=""

# Check if VLAD_configs.sh exists
if [[ -f "$vlad_config" ]]; then
    echo "VLAD_configs.sh found. Reading configuration paths..."

    # Read the paths to config_global.sh files into an array
    config_files=()
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        config_files+=("$line")
    done <"$vlad_config"

    # Display the COHORTs with numbers
    echo "Available COHORTs:"
    for i in "${!config_files[@]}"; do
        config_file="${config_files[$i]}"
        if [[ -f "$config_file" ]]; then
            # Read the cohort name in a subshell so this does not alter active state.
            cohort_name=$(
                unset COHORT
                source "$config_file" || exit 1
                printf '%s' "$COHORT"
            )
            if [[ $? -eq 0 && -n "$cohort_name" ]]; then
                echo "$((i + 1))) COHORT: $cohort_name (File: $config_file)"
            else
                echo "$((i + 1))) Error: Config file $config_file has no valid COHORT."
            fi
        else
            echo "$((i + 1))) Error: Config file $config_file not found."
        fi
    done

    # Option to create a new config
    echo "$((${#config_files[@]} + 1))) Create a new configuration (cohort)"

    # Prompt the user to select an option
    echo -n "Select a COHORT number to source (1-${#config_files[@]}) or create a new one: "
    read -r selection

    # Validate user input
    if [[ $selection -ge 1 && $selection -le ${#config_files[@]} ]]; then
        selected_file="${config_files[$((selection - 1))]}"

        if [[ -f "$selected_file" ]]; then
            active_global_config="$selected_file"
        else
            echo "Error: Selected config file $selected_file not found."
            exit 1
        fi
    elif [[ $selection -eq $((${#config_files[@]} + 1)) ]]; then
        # Create a new config
        if ! create_new_config; then
            echo "There has been a bug, exiting"
            exit 1
        else
            active_global_config="${GLOBAL_DIR}/config_global.sh"
            echo "$active_global_config" >>"$vlad_config"
        fi
    else
        echo "Invalid selection. Exiting."
        exit 1
    fi
else
    echo "VLAD_configs.sh not found. Creating a new one..."
    mkdir -p "$(dirname "$vlad_config")"
    touch "$vlad_config"
    if ! create_new_config; then
        echo "There has been a bug, exiting"
        exit 1
    else
        active_global_config="${GLOBAL_DIR}/config_global.sh"
        echo "$active_global_config" >>"$vlad_config"
    fi
fi

if ! load_global_config "$active_global_config"; then
    echo "Unable to activate the selected cohort. Exiting." >&2
    exit 1
fi
echo "Sourced: $active_global_config"

#############GLOBAL#######
# Associative array to store variable names and values

################ For each MRI type ###################
#Debug
echo "We are in ${GLOBAL_DIR}"

    while true; do
    read -p "Now, what are we doing ? Type : mrsi OR structural OR dti OR exit if everything is done. It will come back here when you've done something. Answer : " mri
    case $mri in
    mrsi)
        echo "Let's configure MRSI ! :-)"
        METAB=('CrPCr' 'NAA' 'NAAG' 'GPCPCh' 'Ins' 'Glu' 'Gln' 'GABA' 'GSH')
        FILES_MRSI_MET=('signal_filtbiharmonic_pvcorr' 'crlb')
        FILES_MRSI_GLOBAL=('fwhm' 'snr')

        mrsi_do
        ;;
    structural)
        echo "Let's configure structural MRI ! :-)"
        FILES_STRUCTURAL=("mwp1" "mwp2" "wp1" "wp2")

        structural_do
        ;;
    dti)
        echo "Let's configure DTI ! :-) "
        FILES_DTI=("gFA")

        dti_do
        ;;
    exit)
        echo "Goodbye :-)"
        exit 0
        ;;
    esac

done
