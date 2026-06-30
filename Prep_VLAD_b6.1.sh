#!/bin/bash

METABSUM3T=("NAANAAG" "CrPCr" "GPCPCh" "Ins" "GluGln")
METABSUM7T5=("NAANAAG" "GluGln")
METABSUM7T9=("NAA" "NAAG" "CrPCr" "Glu" "Gln" "Ins" "GPCPCh" "GSH" "GABA")

OVERWRITE=false
B0_MODE="3"

while [[ $# -gt 0 ]]; do
    case "$1" in
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

# current_dir="/Volumes/MindfulMRSI"
# NAME="bestofsubjects"
# CARDS="/Volumes/MindfulMRSI/Aligned"

# SUBJECTS=($(cat ${current_dir}/Listes/List_${NAME}.txt))

#CREATION OF SUBJECTS FILE FOR VLAD. HE WILL BASED ALL THE 4D cards on this. EACH FILE used SHOULD BE DONE HERE.

#Create an option to delete a previous configuration file

# WHAT WE NEED TO ADD :
# - Verification of bash version (>4)
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
    local acquisition=$4
    local description=$5
    local compressed=$6
    local space_label=${7:-mni}
    local extra_tags=${8:-}

    subject=$(trim_whitespace "$subject")
    visit=$(trim_whitespace "$visit")
    data_type=$(trim_whitespace "$data_type")
    acquisition=$(trim_whitespace "$acquisition")
    description=$(trim_whitespace "$description")
    compressed=$(trim_whitespace "$compressed")
    space_label=$(trim_whitespace "$space_label")
    extra_tags=$(trim_whitespace "$extra_tags")

    if [[ $# -lt 6 || $# -gt 8 ]]; then
        echo "name_subject_file badly used, this will cause some errors. Exiting"
        exit 1
    fi

    local tag_suffix="_space-${space_label}"
    if [[ -n "$extra_tags" ]]; then
        for tag in $extra_tags; do
            tag_suffix+="_${tag}"
        done
    fi

    echo "sub-${subject}_ses-${visit}${tag_suffix}_acq-${acquisition}_desc-${description}_${data_type}.nii${compressed}"

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

compare_nifti_files() {
    local file1="$1"
    local file2="$2"

    local dim1_file1 dim2_file1 dim3_file1
    local dim1_file2 dim2_file2 dim3_file2
    local pixdim1_file1 pixdim2_file1 pixdim3_file1
    local pixdim1_file2 pixdim2_file2 pixdim3_file2

    dim1_file1=$(fslval "$file1" dim1)
    dim2_file1=$(fslval "$file1" dim2)
    dim3_file1=$(fslval "$file1" dim3)

    dim1_file2=$(fslval "$file2" dim1)
    dim2_file2=$(fslval "$file2" dim2)
    dim3_file2=$(fslval "$file2" dim3)

    pixdim1_file1=$(fslval "$file1" pixdim1)
    pixdim2_file1=$(fslval "$file1" pixdim2)
    pixdim3_file1=$(fslval "$file1" pixdim3)

    pixdim1_file2=$(fslval "$file2" pixdim1)
    pixdim2_file2=$(fslval "$file2" pixdim2)
    pixdim3_file2=$(fslval "$file2" pixdim3)

    if [[ "$dim1_file1" == "$dim1_file2" && "$dim2_file1" == "$dim2_file2" && "$dim3_file1" == "$dim3_file2" &&
        "$pixdim1_file1" == "$pixdim1_file2" && "$pixdim2_file1" == "$pixdim2_file2" && "$pixdim3_file1" == "$pixdim3_file2" ]]; then
        echo 0
    else
        echo 1
    fi
}

trim_whitespace() {
    local str="$1"
    str="${str#"${str%%[![:space:]]*}"}"
    str="${str%"${str##*[![:space:]]}"}"
    printf '%s' "$str"
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

run_resample_and_copy_mask() {
    local mask_path="$1"
    local reference_image="$2"
    local destination="$3"

    if ! should_create_file "$destination"; then
        return 0
    fi

    if [[ -z "$script_dir" ]]; then
        echo "script_dir is not defined. Cannot call resample_masks_b3_replace.py"
        return 1
    fi

    local resample_output
    if ! resample_output=$(python3 "${script_dir}/resample_masks_b3_replace.py" --masks "$mask_path" --t1 "$reference_image" --interp nearest 2>&1); then
        echo "Resampling failed for $mask_path"
        echo "$resample_output"
        return 1
    fi

    local resampled_file
    resampled_file=$(echo "$resample_output" | awk -F": " '/Wrote resampled mask to:/ {print $2}' | tail -n 1)
    if [[ -z "$resampled_file" || ! -f "$resampled_file" ]]; then
        echo "Unable to locate resampled mask for $mask_path"
        echo "$resample_output"
        return 1
    fi

    cp "$resampled_file" "$destination"
    rm -f "$resampled_file"
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
        local dtispace=${DTI_SPACE_LABEL:-mni}
        local dtiextra=${DTI_EXTRA_TAGS:-}
        local dtisuffix=${DTI_DATA_SUFFIX:-dwi}
        local dtiacq=${DTI_ACQ_LABEL:-dti}
        candidate=$(name_subject_file "$sub" "$visit" "$dtisuffix" "$dtiacq" "$desc" "$DTI_COMPRESSED" "$dtispace" "$dtiextra")
        search_dir="$NII_DIR_DTI"
        ;;
    structural)
        local structspace=${STRUCTURAL_SPACE_LABEL:-mni}
        local structextra=${STRUCTURAL_EXTRA_TAGS:-}
        local structsuffix=${STRUCTURAL_DATA_SUFFIX:-T1w}
        local structacq=${STRUCTURAL_ACQ_LABEL:-memprage}
        candidate=$(name_subject_file "$sub" "$visit" "$structsuffix" "$structacq" "$desc" "$STRUCTURAL_COMPRESSED" "$structspace" "$structextra")
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

    local mask_entries=()
    if [[ -f "$mask_list" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            local entry
            entry=$(trim_whitespace "$line")
            [[ -z "$entry" || "$entry" == \#* ]] && continue
            mask_entries+=("$entry")
        done <"$mask_list"
    else
        IFS='|' read -r -a mask_entries <<<"$mask_list"
    fi

    local parsed_entries=()
    for entry in "${mask_entries[@]}"; do
        entry=$(trim_whitespace "$entry")
        [[ -z "$entry" ]] && continue
        parsed_entries+=("$entry")
    done
    mask_entries=("${parsed_entries[@]}")

    if [[ ${#mask_entries[@]} -eq 0 ]]; then
        echo "No valid masks specified for ${modality}, skipping."
        return 0
    fi

    local masks_root="${GLOBAL_DIR}/Masques"
    local cohort_name="${COHORT:-default}"
    local dest_dir="${masks_root}/${cohort_name}/${modality}"
    mkdir -p "$dest_dir"

    echo "Preparing masks for ${modality} in $dest_dir"

    for entry in "${mask_entries[@]}"; do
        entry=$(trim_whitespace "$entry")
        [[ -z "$entry" ]] && continue
        local mask_path
        mask_path=$(resolve_mask_source_path "$entry" "$dest_dir")
        if [[ -z "$mask_path" || ! -f "$mask_path" ]]; then
            echo "Mask entry '$entry' not found. Skipping."
            continue
        fi

        local mask_name
        mask_name=$(basename "$mask_path")
        local destination="${dest_dir}/${mask_name}"

        if ! should_create_file "$destination"; then
            continue
        fi

        if [[ $(compare_nifti_files "$mask_path" "$reference_image") -eq 0 ]]; then
            cp "$mask_path" "$destination"
            echo "Copied already aligned mask ${mask_name} to ${destination}"
        else
            run_resample_and_copy_mask "$mask_path" "$reference_image" "$destination"
        fi
    done
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

# Function to prompt for a directory path with graphical interface (zenity)
prompt_for_directory() {
    local varname=$1
    local prompt=$2
    local default_value=$3

    echo "$prompt"

    # Use zenity to display a folder selection dialog, suppress errors
    value=$(zenity --file-selection --directory --title="$prompt" --height=500 --filename="$default_value/" 2>/dev/null)

    # Check if user canceled the selection
    if [[ -z "$value" ]]; then
        echo "No directory selected. Aborting."
        exit 1
    fi

    config_values[$varname]="$value"
}

# Function to prompt for a file path with graphical interface (zenity)
prompt_for_file() {
    local varname=$1
    local prompt=$2
    local default_value=$3

    echo "$prompt"

    value=$(zenity --file-selection --title="$prompt" --filename="$default_value" 2>/dev/null)

    if [[ -z "$value" ]]; then
        echo "No file selected. Aborting."
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

# Function to prompt for one or more masks (zenity, multi-selection)
prompt_for_masks() {
    local varname=$1
    local prompt=$2
    local default_dir=$3

    echo "$prompt"

    local start_dir="${default_dir:-$GLOBAL_DIR}"
    if [[ -z "$start_dir" ]]; then
        start_dir="$HOME"
    fi
    start_dir="${start_dir%/}/"

    local selection
    selection=$(zenity --file-selection --multiple --separator="|" --title="$prompt" --filename="$start_dir" 2>/dev/null)

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

    for var in "${!config_values[@]}"; do
        echo "$var=\"${config_values[$var]}\"" >>config_${type}.sh
    done
}

#Function to find files in subject directory

##############################################################
################### GLOBAL ################################
##############################################################
# Main function to collect inputs and display summary
collect_and_confirm_inputs_global() {
    while true; do
        # Collect inputs using zenity for directories/files and terminal for text
        config_values[GLOBAL_DIR]=${GLOBAL_DIR}
        prompt_for_value "COHORT" "What is the name of the cohort ?" ""
        prompt_for_file "T1_MNI" "Select the standard space T1 that will be used to display results"
        if [[ ${config_values[T1_MNI]} == *.gz ]]; then
            gunzip -f ${config_values[T1_MNI]}
            config_values[T1_MNI]="${config_values[T1_MNI]%.gz}"
        fi

        prompt_for_directory "DIR_RANALYSES" "Where do you want your results from R be stored ?" ""
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

    GLOBAL_DIR=$(zenity --file-selection --directory --title="Global dir" --filename="$HOME" 2>/dev/null)

    # Check if user canceled the selection
    if [[ -z "$GLOBAL_DIR" ]]; then
        echo "No directory selected. Aborting."
        exit 1
    fi

    cd ${GLOBAL_DIR}
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
            source config_global.sh
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
    cd ${GLOBAL_DIR}
    mkdir -p Cartes4D
    mkdir -p Masques
    mkdir -p Results
    mkdir -p Scripts
    mkdir -p Listes
}

##############################################################
###################   MRSI    ################################
##############################################################
 
# Main function to collect inputs and display summary
collect_and_confirm_inputs_mrsi() {
    while true; do
        # Collect inputs using zenity for directories/files and terminal for text
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

        # Display the summary in the terminal and prompt for confirmation
        if display_summary_and_confirm; then
            write_config_file mrsi
            echo "METAB=(${METAB[@]})" >>config_mrsi.sh
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

    if [[ ${acquisition} == "fwhm" || ${acquisition} == "snr" ]]; then
        local spec=true
    else
        local spec=false
    fi
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

                file_path=$(name_subject_file_mrsi ${sub} ${visit} ${data_type} ${MET} ${acquisition} ${compressed})

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
            file_path=$(name_subject_file_mrsi_spec ${sub} ${visit} ${data_type} ${acquisition} ${compressed})

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

    cd ${GLOBAL_DIR}
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

        file_name=$(name_subject_file_mrsi ${sub} ${visit} mrsi ${METAB[0]} ${file} ${MRSI_COMPRESSED})
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

        directory_towork=$(dirname $FILE_PATH)

        cd $directory_towork

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
                add_command="-add $(name_subject_file_mrsi $sub $visit mrsi ${active_metabs[i]} $file ${MRSI_COMPRESSED})"
                COMMAND_SUM+=("$add_command")
            done
            sum_command="$(name_subject_file_mrsi $sub $visit mrsi ${sum_label} $file ${MRSI_COMPRESSED})"
            COMMAND_SUM+=("$sum_command")

            sum_ok=false
            if should_create_file "${sum_command}"; then
                if fslmaths ${COMMAND_SUM[@]}; then
                    sum_ok=true
                else
                    echo "Error for ${sum_label} for $SUBJ"
                fi
            else
                sum_ok=true
            fi

            if [[ "${sum_ok}" == true ]]; then
                echo "Mode ${MODE}: Doing RatioSum"
                for MET in "${ratio_targets[@]}"; do
                    echo "RatioSum of $MET"
                    ratio_out=$(name_subject_file_mrsi ${sub} ${visit} "mrsi" "${MET}${ratio_suffix}" ${file} ${MRSI_COMPRESSED})
                    status=0
                    if should_create_file "$ratio_out"; then
                        fslmaths $(name_subject_file_mrsi ${sub} ${visit} "mrsi" ${MET} ${file} ${MRSI_COMPRESSED}) -div $(name_subject_file_mrsi ${sub} ${visit} "mrsi" "${sum_label}" ${file} ${MRSI_COMPRESSED}) "$ratio_out"
                        status=$?
                    fi
                    if [[ $status -eq 0 ]]; then
                        METABSUM_ACTIVE+=("${MET}${ratio_suffix}")
                        append_unique METABSUM_EXPORT "${MET}${ratio_suffix}"
                    else
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

                    quotient_out=$(name_subject_file_mrsi $sub $visit mrsi ${METABQUOTIENT} $file ${MRSI_COMPRESSED})
                    status=0
                    if should_create_file "$quotient_out"; then
                        fslmaths $(name_subject_file_mrsi $sub $visit mrsi ${numerator} $file ${MRSI_COMPRESSED}) -div $(name_subject_file_mrsi $sub $visit mrsi ${denominator} $file ${MRSI_COMPRESSED}) "$quotient_out"
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
            fslmaths $(name_subject_file_mrsi_spec $sub $visit mrsi snr ${MRSI_COMPRESSED}) -nan -thr ${SNR_Thresh} -bin QMask_SNR_${sub}_${visit}
            fslmaths $(name_subject_file_mrsi_spec $sub $visit mrsi fwhm ${MRSI_COMPRESSED}) -nan -uthr ${FWHM_Thresh} -bin QMask_FWHM_${sub}_${visit}

            for MET in ${METAB[@]}; do
                qmask_out=$(name_subject_file_mrsi $sub $visit mrsi ${MET} QMask ${MRSI_COMPRESSED})
                if should_create_file "$qmask_out"; then
                    fslmaths $(name_subject_file_mrsi $sub $visit mrsi ${MET} crlb ${MRSI_COMPRESSED}) -nan -uthr ${CRLB_Thresh} -bin QMask_CRLB_${MET}_${sub}_${visit}
                    fslmaths QMask_SNR_${sub}_${visit} -mul QMask_FWHM_${sub}_${visit} -mul QMask_CRLB_${MET}_${sub}_${visit} "$qmask_out"
                fi
            done

            if [[ ${#product_metabs[@]} -gt 0 ]]; then
                COMMAND_SUM_MASK=()
                first_command="$(name_subject_file_mrsi "${sub}" "${visit}" "mrsi" "${product_metabs[0]}" "QMask" "${MRSI_COMPRESSED}")"

                COMMAND_SUM_MASK=("$first_command")
                for ((i = 1; i < ${#product_metabs[@]}; i++)); do
                    add_command="-mul $(name_subject_file_mrsi $sub $visit mrsi ${product_metabs[i]} QMask ${MRSI_COMPRESSED})"
                    COMMAND_SUM_MASK+=("$add_command")

                done
                sum_command="$(name_subject_file_mrsi $sub $visit mrsi ${sum_label} QMask ${MRSI_COMPRESSED})"
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
    cd $GLOBAL_DIR

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

    cd ${GLOBAL_DIR}
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
        check_for_file=$(find_files_mrsi ${NII_DIR_MRSI} mrsi ${file_mrsi} ${MRSI_COMPRESSED})
        echo "check_for_file : $check_for_file"
        if [[ "$(echo -n "${check_for_file}")" == "false" ]]; then
            echo "$file_mrsi is missing some subjects. There will be no validation for this file"
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

    for file_mrsi in ${FILES_MRSI_GLOBAL[@]}; do
        find_files_mrsi ${NII_DIR_MRSI} mrsi ${file_mrsi} ${MRSI_COMPRESSED}
        check_for_file=$(find_files_mrsi ${NII_DIR_MRSI} mrsi ${file_mrsi} ${MRSI_COMPRESSED})
        if [[ "$(echo -n "${check_for_file}")" == "false" ]]; then
            echo "$file_mrsi is missing some subjects. There will be no validation for this file"
            read -p "Files are missing : Do you want to continue ? You might experienced some bugs. Check the file not_found.txt in your main directory to see which subjects bugged. Continue ? :" continue
            if [[ $continue == "y" || $continue == "yes" ]]; then
                echo "ok !"
            else
                echo "See you soon"
                exit 1
            fi

        elif [[ "$(echo -n "${check_for_file}")" == "true" ]]; then
            echo "All good, all the files $file_mrsi are where we are expecting them and are validated ! :-)"
        else
            echo "We have a bug, aborting"
            exit 1

        fi
    done

    cd ${GLOBAL_DIR}
    echo "MRSI_FILES_VALIDATED=(${MRSI_FILES_VALIDATED[@]})" >>config_mrsi.sh

    if [[ -n "${MRSI_MASK_LIST}" && ${#MRSI_FILES_VALIDATED[@]} -gt 0 ]]; then
        local reference_mrsi
        reference_mrsi=$(get_reference_image_path "mrsi" "${MRSI_FILES_VALIDATED[0]}")
        if [[ -n "$reference_mrsi" ]]; then
            configure_masks_for_modality "mrsi" "${MRSI_MASK_LIST}" "$reference_mrsi"
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

    cd ${GLOBAL_DIR}
    echo "METAB=(${METAB_EXPORT[@]})" >>config_mrsi.sh
    echo "METABSUM=(${METABSUM_EXPORT[@]})" >>config_mrsi.sh
    echo "METABQUOTIENTS=(${METABQUOTIENTS_EXPORT[@]})" >>config_mrsi.sh
}

##############################################################
################### DTI ################################
##############################################################

# Main function to collect inputs and display summary

collect_and_confirm_inputs_dti() {
    while true; do
        # Collect inputs using zenity for directories/files and terminal for text
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

        prompt_for_value "DTI_SPACE_LABEL" "What is the value after space- in your file names for DTI data ?" "mni"
        prompt_for_value "DTI_EXTRA_TAGS" "List additional BIDS tags (ex: res-3.0mm) to insert after space (separate with spaces, leave blank for none)" ""
        prompt_for_value "DTI_ACQ_LABEL" "Global pattern between space- and desc- in your DTI filenames (ex: dti)" "dti"
        prompt_for_value "DTI_DATA_SUFFIX" "Suffix after desc-..._ in your DTI filenames (ex: dwi)" "dwi"

        echo "WARNING : gFA files are maybe named wierdly by me... Feel free to change the script in further use if needed"
        read -p "By default, the processed files that will be processed are : gFA. If you want to change this list, please answer yes. Any other answer will continue. Answer : " input
        if [[ ${input} == "yes" || ${input} == "y" ]]; then
            read -p "Please type ALL the files, including the ones by default if you want them, that will be present in your files. Separate each one by space. Careful, case senitive. Answer : " newfiles
            FILES_DTI=(${newfiles})
        fi

        prompt_for_masks "DTI_MASK_LIST" "Select the masks to prepare for DTI (use Ctrl/Cmd for multiple, Cancel to skip)" "${GLOBAL_DIR}/Masques"

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
    local acquisition=$3
    local description=$4
    local compressed=$5
    local space_label=${6:-mni}
    local extra_tags=${7:-}

    input_dir=$(trim_whitespace "$input_dir")
    data_type=$(trim_whitespace "$data_type")
    acquisition=$(trim_whitespace "$acquisition")
    description=$(trim_whitespace "$description")
    compressed=$(trim_whitespace "$compressed")
    space_label=$(trim_whitespace "$space_label")
    extra_tags=$(trim_whitespace "$extra_tags")

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

            file_path=$(name_subject_file "${sub}" "${visit}" "${data_type}" "${acquisition}" "${description}" "${compressed}" "${space_label}" "${extra_tags}")
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
    cd $GLOBAL_DIR

    # Check if config file exists and prompt in the terminal for overwriting
    if [[ -f config_dti.sh ]]; then
        read -p "config_dti.sh already exists. Do you want to overwrite it? (yes/no): " overwrite
        if [[ "$overwrite" != "yes" && "$overwrite" != "y" ]]; then
            echo "Aborting."
            exit 1
        fi
    fi

    collect_and_confirm_inputs_dti

    cd $GLOBAL_DIR
    source config_dti.sh

    local dtispace=${DTI_SPACE_LABEL:-mni}
    local dtiextra=${DTI_EXTRA_TAGS:-}
    local dtiacq=${DTI_ACQ_LABEL:-dti}
    local dtisuffix=${DTI_DATA_SUFFIX:-dwi}

    DTI_FILES_VALIDATED=()
    for file_dti in ${FILES_DTI[@]}; do
        check_for_file=""
        not_found=()
        check_for_file=$(find_files "${NII_DIR_DTI}" "${dtisuffix}" "${dtiacq}" "${file_dti}" "${DTI_COMPRESSED}" "${dtispace}" "${dtiextra}")
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
        local reference_dti
        reference_dti=$(get_reference_image_path "dti" "${DTI_FILES_VALIDATED[0]}")
        if [[ -n "$reference_dti" ]]; then
            configure_masks_for_modality "dti" "${DTI_MASK_LIST}" "$reference_dti"
        else
            echo "Unable to determine a DTI reference image for mask validation."
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
        # Collect inputs using zenity for directories/files and terminal for text
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

        prompt_for_value "STRUCTURAL_SPACE_LABEL" "What is the value after space- in your file names for STRUCTURAL data ?" "mni"
        prompt_for_value "STRUCTURAL_EXTRA_TAGS" "List additional BIDS tags (ex: res-3.0mm) to insert after space (separate with spaces, leave blank for none)" ""
        prompt_for_value "STRUCTURAL_ACQ_LABEL" "Global pattern between space- and desc- in your STRUCTURAL filenames (ex: memprage)" "memprage"
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
    cd $GLOBAL_DIR

    # Check if config file exists and prompt in the terminal for overwriting
    if [[ -f config_structural.sh ]]; then
        read -p "config_structural.sh already exists. Do you want to overwrite it? (yes/no):  " overwrite
        if [[ "$overwrite" != "yes" && "$overwrite" != "y" ]]; then
            echo "Aborting."
            exit 1
        fi
    fi

    collect_and_confirm_inputs_structural

    cd $GLOBAL_DIR
    source config_structural.sh

    local structspace=${STRUCTURAL_SPACE_LABEL:-mni}
    local structextra=${STRUCTURAL_EXTRA_TAGS:-}
    local structacq=${STRUCTURAL_ACQ_LABEL:-memprage}
    local structsuffix=${STRUCTURAL_DATA_SUFFIX:-T1w}

    STRUCTURAL_FILES_VALIDATED=()
    for file_structural in ${FILES_STRUCTURAL[@]}; do
        check_for_file=""
        not_found=""
        check_for_file=$(find_files "${NII_DIR_STRUCTURAL}" "${structsuffix}" "${structacq}" "${file_structural}" "${STRUCTURAL_COMPRESSED}" "${structspace}" "${structextra}")
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

    cd ${GLOBAL_DIR}
    echo "STRUCTURAL_FILES_VALIDATED=(${STRUCTURAL_FILES_VALIDATED[@]})" >>config_structural.sh


    if [[ -n "${STRUCTURAL_MASK_LIST}" && ${#STRUCTURAL_FILES_VALIDATED[@]} -gt 0 ]]; then
        local reference_struct
        reference_struct=$(get_reference_image_path "structural" "${STRUCTURAL_FILES_VALIDATED[0]}")
        if [[ -n "$reference_struct" ]]; then
            configure_masks_for_modality "structural" "${STRUCTURAL_MASK_LIST}" "$reference_struct"
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

echo "================================="
echo "Hi ! Let's create and organize the files you need ! :-)"
echo "First, are you sure that your files are named correctly."
echo "Structure that we want : "
echo "sub: subject ; ses : session/visit, if only one put V1 ; space : original, MNI152, etc... ; acq : acquisition which is the specificity of how the MRI worked, desc : description of the file (more liberal ^^)"
echo "Example 1 : sub-SUBJECT_ses-SESSION_space-MNI_acq-ACQUISITION_desc-MET_spectroscopy.nii.gz"
echo "Example 2 : sub-SUBJECT_space-MNI_acq-memperage_desc-mwp1_T1.nii.gz"
read -p " If they are not in this structure, answer exit and use the examples provided to rename your files in the most standard way possible to be able to continue further. Anything else will continue.  " answer
if [[ $answer == "exit" ]]; then
    echo "Well good luck and see you soon here. Tip : pompt chatGPT or Claude with the current structure of your files and the structure you want to have, it will do the script(s) :-)"
    exit 0
else
    echo "Let's proceed to the rest"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Path to VLAD_configs.sh
vlad_config="VLAD_configs.txt"

# Check if VLAD_configs.sh exists
if [[ -f "$vlad_config" ]]; then
    echo "VLAD_configs.sh found. Reading configuration paths..."

    # Read the paths to config_global.sh files into an array
    config_files=()
    while IFS= read -r line; do
        config_files+=("$line")
    done <"$vlad_config"

    # Display the COHORTs with numbers
    echo "Available COHORTs:"
    for i in "${!config_files[@]}"; do
        config_file="${config_files[$i]}"
        if [[ -f "$config_file" ]]; then
            # Source the config file to get the COHORT variable
            source "$config_file"
            echo "$((i + 1))) COHORT: $COHORT (File: $config_file)"
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

        # Source the selected config file
        if [[ -f "$selected_file" ]]; then
            source "$selected_file"
            echo "Sourced: $selected_file"
        else
            echo "Error: Selected config file $selected_file not found."
        fi
    elif [[ $selection -eq $((${#config_files[@]} + 1)) ]]; then
        # Create a new config
        create_new_config
        if [[ $? -ne 0 ]]; then
            echo "There has been a bug, exiting"
            exit 1
        else
            echo "${GLOBAL_DIR}/config_global.sh" >>${script_dir}/VLAD_configs.txt
        fi
    else
        echo "Invalid selection. Exiting."
    fi
else
    echo "VLAD_configs.sh not found. Creating a new one..."
    touch "$vlad_config"
    create_new_config
    if [[ $? -ne 0 ]]; then
        echo "There has been a bug, exiting"
        exit 1
    else
        echo "${GLOBAL_DIR}/config_global.sh" >>${script_dir}/VLAD_configs.txt
    fi
fi

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
