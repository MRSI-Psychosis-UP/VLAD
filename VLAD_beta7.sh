#!/bin/bash
#----------------------------
#VLAD for MRSI : The final script
#----------------------------
#Version log
# [x] Randomise with sum / ratio, optional
# [x] Auto extraction -> Working
# [x] File with significant anaylses -> Working
# [x] 4D files are taken directly from Cartes4D and not anymore copied -> Working + temp files with quali mask
# [x] Randomise with GM or WM mask only + quality masks and lovely thresholds -> Working. Pretty useless.
# Alpha 2 : 15.09.23 : first actual try to make this work
# Alpha 3 : 13.12.23 : Lots of correction following changes in the preprocess
# Alpha 4 : 13.12.23 : With the possibility to add WM / GM masks and now thresholded on the quality masks
# Alpha 5 : 22.12.23 : Begining of the R integration
# Alpha 6 : 02-03.24 : Last version with the previous pre-processing

# Alpha 7 : 05.03.24 : New version, almost unreconizable
#           - Preprocessing is now minimal since it's done in another way. All will be implemented here
#           - Creation of List + matrix is going to be automated
#           - Now we will trust the filled versions completely
# Alpha 8 : 07.03.24 : Alpha 7 working great. With alpha 8, masking the 4D cards with the quali mask to load less in randomise. Looking for making the confirmation works.
# Alpha 9 : 15.03.24 : [x] New versions of snapshots + new version of matrix exportation (exports all the Excel table to R). [x] Correction to the name /matrix name if they
#                      have an underscore (bugs in R). [x] Possibility to do analyses on the SumMetabs file (-> very ugly for the moment)
# Alpha 10 : 07.06.24 : Back in business ! [x] New masks that doesn't need to be resliced and are far more accurate \o/
#   > Working fine on 21.06.24             [x] Start of the ratios 1metab/1metab implementation
#   > Errors corrected on 26.06            [x] Refinements do be done on the script that makes the matrix (possibility to remove rows without information + now checking for collinerarity)
#                                          [x] Implementation of confirmation analyses to be run on cluster + a No-warning option for this script to be runned on cluster
#                                          [x] Refinements to the code, notably for the SumMetabs treatment
#
# Alpha 11 : 03.07.24 : [-] Now possibility to run everything on Lipid08, change of directory, not very nice but well...
#Implemented along the route :
# [x] Extraction from each lobe -> ROI extract script. To see if we integrate it here, doesn't seem to have sense
# [x] Auto-integrate in R -> Working
# [x] Auto-plot in R -> Working. Could be more complex, but simple analyses working great.
# [x] Files in Markdown -> Working. Bad design for the moment.
# [-] Start to make a config file to make the script exportable to other machines -> active in alpha11
# [x] Find a way to replace the SPM Matlab reslice. No equivalent found in FSL for the moment
# [x] Possibility to find significant analyses varying on the p... -> via a warning if p <0.01
# [x] Refinements for R summary (more beautiful and insightful summaries, more beautiful plots);
# [x] Possibility to do ANOVAs
# [x] Launching confirmation analyses (including masked) directly from this script
# [-] One big version to do MRSI, grey-matter and white matter analysis, and compare them (with palm) <- this will take time :-$
#      -> can run the 3 different analyses but 
#----------------------------------
#NOW IT'S VLAD, and we are entering beta !
# BETA 1 : working great
# BETA 2 : current big work in progress, absolutely not viable right now.
# BETA 3 : working great for most of the cases tested. 
# BETA 4 : little refinements especially for the file names to fit the new BIDS of Federico
# BETA 5 : VLAD lov package, working with FSL SWE to make longitudinal analyses. 
# BETA 6 : Global refinements + new features in test (logarithm, warnings...)
#----------------------------------
# Features to ad :
# [ ] Possibility to use palm (for futher versions)
# [ ] Adaptation for DTI -> will be a completely different version
#==============STAY TUNED FOR UPDATES ! =====================
# Next to come : 
#                [ ] System to be more flexible on the files names / organisation... )

#source config_MRSI.sh
#cd ..
# GLOBAL_DIR=$GLOBAL_DIR # THIS HAS TO GOOOOO
# lipidmod_maps=$NII_DIR_DIFF #These are old but for the moment, I don't want to redo everything haha
# lipid08_maps=$NII_DIR_08

######### LOADING CONFIGURATION (1st step)
# Path to the VLAD_configs.txt file
vlad_config="VLAD_configs.txt"

# Function to display available COHORTs and select one
select_cohort() {
    echo "Available COHORTs:"
    for i in "${!config_files[@]}"; do
        config_file="${config_files[$i]}"
        cohort_value=$(grep -E '^COHORT=' "$config_file" | cut -d '=' -f2- | tr -d '"')
        echo "$((i + 1))) COHORT: $cohort_value (File: $config_file)"
    done

    # Prompt the user to select a COHORT
    echo -n "Select a COHORT number to run (1-${#config_files[@]}): "
    read -r selection

    # Validate user input
    if [[ $selection -ge 1 && $selection -le ${#config_files[@]} ]]; then
        selected_file="${config_files[$((selection - 1))]}"
        echo "Selected: $selected_file"
    else
        echo "Invalid selection. Exiting."
        exit 1
    fi
}

# Read the paths to config_global.sh files into an array
if [[ -f "$vlad_config" ]]; then
    config_files=()
    while IFS= read -r line; do
        config_files+=("$line")
    done <"$vlad_config"
else
    echo "Error: VLAD_configs.txt not found. Please, run setup_VLAD.sh first and configure at least one cohort :-)"
    exit 1
fi

trim_whitespace() {
    local str="$1"
    str="${str#"${str%%[![:space:]]*}"}"
    str="${str%"${str##*[![:space:]]}"}"
    printf '%s' "$str"
}

SCRIPT_ABS_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
ORIGINAL_ARGS=("$@")

BATCH_MODE="off"
BATCH_WORKER=false
BATCH_FILE="$(cd "$(dirname "$0")" && pwd)/VLAD_batch_commands.txt"

run_batch_commands() {
    local batch_file="$1"
    local remaining_file
    local batch_line
    local trimmed_line
    local command_index=0
    local success_count=0
    local failed_count=0

    if [[ ! -e "$batch_file" ]]; then
        echo "Batch file $batch_file does not exist. Nothing to run."
        return 1
    fi

    if [[ ! -s "$batch_file" ]]; then
        echo "Batch file $batch_file is empty. Nothing to run."
        return 0
    fi

    remaining_file=$(mktemp "${TMPDIR:-/tmp}/vlad_batch_remaining.XXXXXX")

    while IFS= read -r batch_line || [[ -n "$batch_line" ]]; do
        trimmed_line=$(trim_whitespace "$batch_line")
        if [[ -z "$trimmed_line" || "${trimmed_line:0:1}" == "#" ]]; then
            printf '%s\n' "$batch_line" >>"$remaining_file"
            continue
        fi

        ((command_index++))
        echo "Running batch command ${command_index}: ${trimmed_line}"
        if bash -lc "$trimmed_line"; then
            ((success_count++))
            echo "Command ${command_index} finished successfully and was removed from queue."
        else
            ((failed_count++))
            echo "Command ${command_index} failed and will stay in queue."
            printf '%s\n' "$batch_line" >>"$remaining_file"
        fi
    done <"$batch_file"

    mv "$remaining_file" "$batch_file"
    echo "Batch summary: ${success_count} succeeded, ${failed_count} failed."

    if [[ $failed_count -gt 0 ]]; then
        return 1
    fi
    return 0
}

build_batch_command() {
    local cohort_to_force="$1"
    local command_to_queue=""
    local arg=""
    local arg_quoted=""
    local skip_next=false
    local saw_cohort=false
    local i=0

    printf -v command_to_queue "%q" "$SCRIPT_ABS_PATH"

    while [[ $i -lt ${#ORIGINAL_ARGS[@]} ]]; do
        arg="${ORIGINAL_ARGS[$i]}"

        if [[ "$skip_next" == true ]]; then
            skip_next=false
            ((i++))
            continue
        fi

        case "$arg" in
        --prepare | --analyze)
            ;;
        --batch | --batch-file)
            skip_next=true
            ;;
        -c | --cohort)
            saw_cohort=true
            if [[ $((i + 1)) -lt ${#ORIGINAL_ARGS[@]} ]]; then
                printf -v arg_quoted " %q" "$arg"
                command_to_queue+="$arg_quoted"
                printf -v arg_quoted " %q" "${ORIGINAL_ARGS[$((i + 1))]}"
                command_to_queue+="$arg_quoted"
                skip_next=true
            fi
            ;;
        *)
            printf -v arg_quoted " %q" "$arg"
            command_to_queue+="$arg_quoted"
            ;;
        esac
        ((i++))
    done

    if [[ "$saw_cohort" == false && -n "$cohort_to_force" ]]; then
        printf -v arg_quoted " %q" "--cohort"
        command_to_queue+="$arg_quoted"
        printf -v arg_quoted " %q" "$cohort_to_force"
        command_to_queue+="$arg_quoted"
    fi

    printf -v arg_quoted " %q" "--batch"
    command_to_queue+="$arg_quoted"
    printf -v arg_quoted " %q" "worker"
    command_to_queue+="$arg_quoted"

    echo "$command_to_queue"
}

configure_python() {
    local pyenv_prefix

    if [[ -n "${VLAD_PYTHON_BIN}" ]]; then
        if [[ ! -x "${VLAD_PYTHON_BIN}" ]]; then
            echo "Configured Python interpreter is not executable: ${VLAD_PYTHON_BIN}"
            exit 1
        fi
    elif [[ -n "${VLAD_PYTHON_ENV}" ]]; then
        if ! command -v pyenv >/dev/null 2>&1; then
            echo "Cannot load pyenv environment '${VLAD_PYTHON_ENV}': pyenv was not found in PATH."
            exit 1
        fi

        if ! pyenv_prefix=$(pyenv prefix "${VLAD_PYTHON_ENV}" 2>/dev/null); then
            echo "Cannot find pyenv environment '${VLAD_PYTHON_ENV}'."
            exit 1
        fi

        VLAD_PYTHON_BIN="${pyenv_prefix}/bin/python"
        if [[ ! -x "${VLAD_PYTHON_BIN}" ]]; then
            echo "Python executable not found in pyenv environment '${VLAD_PYTHON_ENV}': ${VLAD_PYTHON_BIN}"
            exit 1
        fi
    else
        VLAD_PYTHON_BIN=$(command -v python3 || true)
        if [[ -z "${VLAD_PYTHON_BIN}" ]]; then
            echo "python3 was not found in PATH. Set --python-env, VLAD_PYTHON_ENV, or VLAD_PYTHON_BIN."
            exit 1
        fi
    fi

    echo "Using Python interpreter: ${VLAD_PYTHON_BIN}"
}

append_command_to_batch() {
    local batch_file="$1"
    local command_to_add="$2"
    local batch_dir

    batch_dir=$(dirname "$batch_file")
    mkdir -p "$batch_dir"
    touch "$batch_file"

    if grep -Fxq "$command_to_add" "$batch_file"; then
        echo "Command already present in batch file: $batch_file"
    else
        printf '%s\n' "$command_to_add" >>"$batch_file"
        echo "Command added to batch file: $batch_file"
    fi
}

#####---- Reconstruction of command

# Initialize an empty string to hold the reconstructed command
reconstructed_command=""

# Add the script name or path
printf -v reconstructed_command "%q" "$0"

# Loop through all arguments
for arg in "$@"; do
    # Append each argument to the command string, properly quoted
    printf -v arg_quoted " %q" "$arg"
    reconstructed_command+="$arg_quoted"
done
#####------------------------------

# Function to display the script usage
display_help() {
    echo "Useful informations : "
    echo "	- Preprocess is done completely separately now. This script can do the lists of subject and 4D cards if you ask it or if it doesn't detect them"
    echo "	- Now all the analyses will be done on files that are filled. No sense to do otherwise. Nofill / allfill options are dismissed"
    echo "	- By default randomise will be done with 100 permutations. Use the -p option to change it. Read further to understand this low number"
    echo "	- For the moment, only works for group difference or quantitative values. Other options will be added if needed. Detection of type of analysis is automatic"
    echo "  - To stop randomise analysing voxels that don't have enough \"valuable\" values, we multiply the WM/GM masks with a global quality mask. By default, it take into acount the voxel if >68% of the participant have a value. Value can be change with --qualithresh"
    echo "  - Auto-integration in R done. Rmarkdown file could be prettier, but it works until refinements."

    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  -h, --help               Display this help message"
    echo "  -c, --cohort             The cohort on which you are working. Remember to launch setup_VLAD.sh before otherwise this script won't work"
    echo "  -t, --type               Type of images you want to analyze : MRSI or DTI or Structural - MANDATORY"
    echo ""
    echo "  -n, --name <name>        Name of the analysis - MANDATORY"
    echo "  -m, --matrix <name>      Name of the matrix"
    echo "  -a, --anova              By default, VLAD is doing a 2 group comparison or linera regression (auto-detected). If you want to do an ANOVA, specify it. It will ONLY do the ANOVA. Currently in work."
    echo "  --b0 <value>             B0/field strength presets for MRSI: 3 (default, legacy SumMetabs), 7 (9 metabs with RatioSum9/SumMetabs9), 7as3 (treat 7T as 3T, use RatioSum5/SumMetabs5 with NAANAAG/GluGln)"
    echo ""
    echo "  --metabs   		         If you want to do only one or more specific metabs on absolute value. If you put \"none\" after it, no absolute metabs will be done. The matabolites mades are different than the one with the RatioSum"
    echo "  -s, --sum [value]        With the sum of metabolites. By default will do the 5 metabolites + SumMetabs, but you can specify the ones you want."
    echo "  -q, --quotients [value]  Metabolites quotients, NAAonCr GlxonCr ChoonCr InsonNAA InsonGlx InsonCho GlxonNAA GlxonCho ChoonNAA. Same as sum, by default all will be done but you can choose some. Globs are supported (quote in zsh, e.g. '*onCrPCr')"
    echo "  -d, --description        ONLY for STRUCTURAL or DTI : select the description of files you want to do the analysis. If nothing is put here, all the validated files will be analyzed"
    echo ""
    echo "  -p, --perm <value>	     Number of permutations By default 300"
    echo "  --cards	[value]          Will force to do the 4D cards. This option also permits to use a different suffix than the default \"filled\" for cards. Just add an argument after it :-). If empty, will do the filled."
    echo "  --mask <value>           Do randomise with a grey matter and white matter mask (on top of a global mask): possible values : wmgm (white and total grey matter) / wmgmbg (same but grey matter separated in cortical and basal ganglia)"
    echo "  --qualithresh <value>    Quality threshold (%of subject) to mask the WM_GM / WM / GM masks on analysis (put the %age of subject, for eg if 50%, put --qualithresh 50)"
    echo "  --smooth                 Force smoothing of cards. Specify the smoothing as the sigma after it, Default = 1 if nothing specified. One 4D card by smoothing sigma will be made"
    echo "  --logarithm              Apply logarithm to the data before randomise"
    echo ""
    echo "  -l, --longitudinal <value> If you are doing a longitudinal analysis. Currently in work. Argument : swe or afni depending on the tool you want to use."
    echo ""
    echo "  --noparallel             By default, randomise will use randomise_parallel, with this option it won't be done"
    echo "  --confirmation <value>   Option to confirm exploratory analyses with perms < 500. This sets option -s, permutations to 5000, disables all warning . Be sure to have solid 4D cards and matrix been done. To force it to do 4D cards or different number of permut put the arguments after this one."
    echo "                           value : little for increasing the number of permutation only and disable the warnings. masked for masking each subject with it qmask"
    echo "                           If you have a doubt : little is ok for DTI and structural. Masked only for hardcore confirmation with MRSI"
    echo "  -cm, --confirmation_M    Create what is necessary to run a confirmation analysis with masked values depending on the qmask"
    #echo "  --difflipid              If you have different lipid suppression. Will remind you to put it in the matrix and will select a different folder to find the maps"
    echo "  --remakeall              Delete the folders associated with the name in Cartes4D/, Results/ and the List"
    echo "  --prepare                Do only the preparation of analysis : List of subjects, Matrix and 4D card. If -cm is specified, it will also do it."
    echo "  --analyze                Do only the analysis part : checking for the Randomise results and doing the sum-up with R"
    echo "  --batch <mode>           Batch workflow mode: queue/add (store analysis command after --prepare), run (execute queued commands), worker (internal)"
    echo "  --batch-file <path>      Optional path for batch command file (default: script directory/VLAD_batch_commands.txt)"
    echo "  --python-env <name>      Use this pyenv environment for VLAD Python scripts (for example: venv-mri)"
    echo ""
    echo "------------------------- "
    echo "Examples"
    echo "--------"
    echo "$0 -c ARMS -t MRSI -n Allsubjects -m PatientsvsCtrl -p 500 -s -q -m wmgmbg | The total"
    echo "$0 -n Allsubjects -m Patients1vsPatients2vsCtrl -a --metabs CrPCr NAANAAG -s NAANAAG -q NAANAAGonCrPCr --confirmation masked | ANOVA on 3 groups for specific metabolites in confirmation mode with masks"
}

# Default values
# WATER=false
NAME=""
MATRIX=""
SUM=false
#REGIONS=($(cat Listes/ROI/lobes.txt))
NBPERMUT=300
#GROUP=false
#CONC=false
MASKS=('white-grey-matter')
QUALITRESH=68
RANALYSE=true
PARALLEL="_parallel"
ONLYORIGINAL=false
DOLIST=false
DO4D=false
MODALITY="FiltBasic"
CONFIRMATION=false
CLUSTER=false # To keep ?
QUOTIENTS=false
CONFIRMATION_MASKS=false
REMAKEALL=false
ANOVA=0
DESCRIPTION=""
SMOOTHSIGMA=1
SMOOTHINGCARDS=false
LOGARITHM=false
LONGITUDINAL=false
B0_MODE="3"
SUM_METAB_LABEL="SumMetabs"
VLAD_PYTHON_ENV="${VLAD_PYTHON_ENV:-}"
VLAD_PYTHON_BIN="${VLAD_PYTHON_BIN:-}"

PREPARE=true
RANDOMISING=true
EXTRACTING=false # By default, false because it will be launched by RANDOMISING true
SUMUP=true

SPACE_LABEL_SETTING="mni"
EXTRA_TAGS_SETTING=""

#mrsi_maps_dir="${lipid08_maps}"
#DIFFLIPID=false -> disabled for now

# METAB=('Cr+PCr' 'GPC+PCh' 'Ins' 'NAA+NAAG' 'Glu+Gln')
# METABSUM=('Cr+PCr' 'GPC+PCh' 'Ins' 'NAA+NAAG' 'Glu+Gln')
# METABQUOTIENTS=('NAAonCr' 'GlxonCr' 'ChoonCr' 'InsonNAA' 'InsonGlx' 'InsonCho' 'GlxonNAA' 'GlxonCho' 'ChoonNAA')

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
    -h | --help)
        display_help
        exit 0
        ;;
    # -w | --water)
    #     WATER=true
    #     shift
    #     ;;
    --confirmation)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing complementary argument for $1."
            display_help
            exit 1
        elif [[ "$2" == "little" ]]; then
            SUM=true
            NBPERMUT=10000
            DO4D=false
            CONFIRMATION=true
        elif [[ "$2" == "masked" ]]; then
            CONFIRMATION=true
            CONFIRMATION_MASKS=true
            NBPERMUT=10000
            ONLYORIGINAL=true
        else
            echo "The argument $2 is not recognized."
            display_help
            exit 1
        fi

        shift 2
        ;;
    -n | --name)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing name of analysis $1"
            display_help
            exit 1
        fi
        NAME="$2"
        shift 2
        ;;
    -c | --cohort)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing name of cohort $1"
            display_help
            exit 1
        fi
        cohort_arg="$2"
        shift 2
        ;;
    -t | --type)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing type of images"
            display_help
            exit 1
        fi
        type_images="$2"
        shift 2
        ;;
    -m | --matrix)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing name of matrix $1"
            display_help
            exit 1
        fi
        MATRIX="$2"
        shift 2
        ;;
    -p | --perm)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing number of permutations $1"
            display_help
            exit 1
        fi
        NBPERMUT=$2
        shift 2
        ;;
    -l | --longitudinal)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing name of tool you want to use for $1"
            display_help
            exit 1
        fi
        LONGITUDINAL=true
        LONGITUDINAL_TOOL="$2"
        shift 2
        ;;
    --qualithresh)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing number for $1"
            display_help
            exit 1
        fi
        if [[ $2 -le 99 ]]; then
            QUALITRESH=$2
            shift 2
        else
            echo "Due to technical limitations, Qualitresh can have a maximum of 99 (for a value of 0.99 or 99%). This will be fixed with python one day ^_^"
            exit 1
        fi
        ;;
    -d | --description)
        if [[ -z $2 || $2 == -* ]]; then
            echo "You probably didn't understand how description works, you should indicate the name of specific data type you want to do (mwp1, gFA, some of that). Skipping and doing everything."
        else
            shift
            DESCRIPTION=()
            while [[ "$1" != "-"* && "$#" -gt 0 ]]; do ####### WE NEED TO MOVE THIS WITH THE NEW CONFIGURATION THING.... AHLALALA
                DESCRIPTION+=("$1")
                shift
            done

        fi
        ;;
    --b0)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing value for $1 (use 3, 7 or 7as3)."
            exit 1
        fi
        case "$2" in
        3 | 7 | 7as3)
            B0_MODE="$2"
            ;;
        *)
            echo "Invalid --b0 value: $2. Allowed: 3, 7, 7as3."
            exit 1
            ;;
        esac
        shift 2
        ;;
    --metabs)
        if [[ -z $2 || $2 == -* ]]; then
            echo "You probably didn't understand how metabs work, you should indicate the name of specific metabs you want to do. Skipping and doing everything."
        else
            shift
            METABprompted=()
            if [[ "$1" == "none" ]]; then
                echo "You asked for no analyze on absolute concentration."
                METABprompted="none"
                shift
            else
                while [[ "$1" != "-"* && "$#" -gt 0 ]]; do ####### WE NEED TO MOVE THIS WITH THE NEW CONFIGURATION THING.... AHLALALA
                    METABprompted+=("$1")
                    shift
                done
            fi
        fi
        ;;
    --mask)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing value for $1"
            display_help
            exit 1
        fi
        mask_choice="$2"
        case $mask_choice in
        wmgm)
            MASKS=('white-grey-matter' 'white-matter' 'grey-matter')
            ;;
        wmgmbg)
            MASKS=('white-grey-matter' 'white-matter' 'cortex' 'subcortical-nuclei')
            ;;
        onlygrey)
            MASKS=('grey-matter')
            ;;
        onlywhite)
            MASKS=('white-matter')
            ;;
        onlygreydetail)
            MASKS=('grey-matter' 'cortex' 'subcortical-nuclei')
            ;;
        cerebellum)
            MASKS=('white-grey-matter-cerebellum')
            ;;
        esac
        shift 2
        ;;
    -s | --sum)
        SUM=true
        shift
        if [[ "$1" != "-"* && "$#" -gt 0 ]]; then
            METABSUMprompted=()
            while [[ "$1" != "-"* && "$#" -gt 0 ]]; do
                METABSUMprompted+=("$1")
                shift
            done
        fi
        ;;
    -q | --quotients)
        QUOTIENTS=true
        shift
        if [[ "$1" != "-"* && "$#" -gt 0 ]]; then
            METABQUOTIENTSprompted=()
            while [[ "$1" != "-"* && "$#" -gt 0 ]]; do
                METABQUOTIENTSprompted+=("$1")
                shift
            done
        fi
        ;;
    --difflipid)
        DIFFLIPID=true
        mrsi_maps_dir="${lipidmod_maps}"
        shift
        ;;
    --smooth)
        SMOOTHINGCARDS=true
        if [[ -z $2 || $2 == -* ]]; then
            echo "Default smoothing at 1"
            shift
        else
            SMOOTHSIGMA=$2
            shift 2
        fi
        ;;
    --logarithm)
        LOGARITHM=true
        shift
        ;;
    -a | --anova)
        ANOVA=1
        shift
        ;;
    -cm | --confirmation_M)
        CONFIRMATION_MASKS=true
        shift
        ;;
    --cards)
        DO4D=true
        if [[ $2 == -* || -z $2 ]]; then
            shift
        else
            MODALITYprompted="$2"
            shift 2
        fi
        ;;
    --noparallel)
        PARALLEL=""
        shift
        ;;
    --remakeall)
        REMAKEALL=true
        shift
        ;;
    --prepare)
        PREPARE=true
        RANDOMISING=false
        EXTRACTING=false
        SUMUP=false
        shift
        ;;
    --analyze)
        PREPARE=false
        RANDOMISING=false
        EXTRACTING=true
        SUMUP=true
        shift
        ;;
    --batch)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing mode for $1 (use queue/add, run or worker)."
            display_help
            exit 1
        fi
        case "$2" in
        queue | add | run | worker)
            BATCH_MODE="$2"
            ;;
        *)
            echo "Unknown batch mode: $2"
            display_help
            exit 1
            ;;
        esac
        shift 2
        ;;
    --batch-file)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing file path for $1"
            display_help
            exit 1
        fi
        if [[ "$2" = /* ]]; then
            BATCH_FILE="$2"
        else
            BATCH_FILE="$(pwd)/$2"
        fi
        shift 2
        ;;
    --python-env)
        if [[ -z $2 || $2 == -* ]]; then
            echo "Missing pyenv environment name for $1"
            display_help
            exit 1
        fi
        VLAD_PYTHON_ENV="$2"
        shift 2
        ;;
    *)
        echo "Invalid argument: $1"
        display_help
        exit 1
        ;;
    esac
done

if [[ "${BATCH_MODE}" == "add" ]]; then
    BATCH_MODE="queue"
fi

if [[ "${BATCH_MODE}" == "worker" ]]; then
    BATCH_WORKER=true
    BATCH_MODE="off"
    PREPARE=false
    RANDOMISING=true
    EXTRACTING=true
    SUMUP=true
fi

if [[ "${BATCH_MODE}" == "run" ]]; then
    run_batch_commands "${BATCH_FILE}"
    exit $?
fi

if [[ "${BATCH_MODE}" == "queue" && ${RANDOMISING} == true ]]; then
    echo "Batch queue mode must be used with --prepare so preparation happens now and analysis is queued for later."
    exit 1
fi

cat <<EOF

_::::::::::::::::::::::__::::::::::::::::::::::__::::::::::::::::::::::__::::::::::::::::::::::__::::::::::::::::::::::_
________________________________________________________________________________________________________________________
                                                                                                       LOV Package                                                                                                                                                                                                                                     
VVVVVVVV           VVVVVVVVLLLLLLLLLLL                            AAA               DDDDDDDDDDDDD                        
V::::::V           V::::::VL:::::::::L                           A:::A              D::::::::::::DDD                    
V::::::V           V::::::VL:::::::::L                          A:::::A             D:::::::::::::::DD                  
V::::::V           V::::::VLL:::::::LL                         A:::::::A            DDD:::::DDDDD:::::D                 
 V:::::V           V:::::V   L:::::L                          A:::::::::A             D:::::D    D:::::D                
  V:::::V         V:::::V    L:::::L                         A:::::A:::::A            D:::::D     D:::::D               
   V:::::V       V:::::V     L:::::L                        A:::::A A:::::A           D:::::D     D:::::D               
    V:::::V     V:::::V      L:::::L                       A:::::A   A:::::A          D:::::D     D:::::D               
     V:::::V   V:::::V       L:::::L                      A:::::A     A:::::A         D:::::D     D:::::D               
      V:::::V V:::::V        L:::::L                     A:::::AAAAAAAAA:::::A        D:::::D     D:::::D               
       V:::::V:::::V         L:::::L                    A:::::::::::::::::::::A       D:::::D     D:::::D               
        V:::::::::V          L:::::L         LLLLLL    A:::::AAAAAAAAAAAAA:::::A      D:::::D    D:::::D                
         V:::::::V         LL:::::::LLLLLLLLL:::::L   A:::::A             A:::::A   DDD:::::DDDDD:::::D                 
          V:::::V          L::::::::::::::::::::::L  A:::::A               A:::::A  D:::::::::::::::DD                  
           V:::V           L::::::::::::::::::::::L A:::::A                 A:::::A D::::::::::::DDD                    
            VVV            LLLLLLLLLLLLLLLLLLLLLLLLAAAAAAA                   AAAAAAADDDDDDDDDDDDD    for "$type_images"

_ _                          ||  _      _          
\\/oxel-based-analysis  for  L_]//\zy  [|)octorants
________________________________________________________________________________________________________________________ 
_::::::::::::::::::::::__::::::::::::::::::::::__::::::::::::::::::::::__::::::::::::::::::::::__::::::::::::::::::::::_
                  
EOF
#################################################
########### Verifications and orientation #######
#################################################

if [[ -z $type_images ]]; then
    echo "We don't know which type of images you want to anaylze you want to do. Please provide it with the argument -t"
    display_help
    exit 1
fi

if [[ -n "$cohort_arg" ]]; then

    # Try to find the corresponding config file for the given cohort argument
    for config_file in "${config_files[@]}"; do
        cohort_value=$(grep -E '^COHORT=' "$config_file" | cut -d '=' -f2- | tr -d '"')
        if [[ "$cohort_value" == "$cohort_arg" ]]; then
            selected_file="$config_file"
            break
        fi
    done

    if [[ -z "$selected_file" ]]; then
        echo "Error: COHORT '$cohort_arg' not found."
        exit 1
    fi
else
    # If no argument is provided, ask the user to select a COHORT
    select_cohort
fi

# Source the selected config file
if [[ -f "$selected_file" ]]; then
    source "$selected_file"
    #echo "Sourced configuration for COHORT: $COHORT"
else
    echo "Error: Config file $selected_file not found."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
: "${RANALYSE_CREATION_SCRIPT:=${SCRIPT_DIR}/Randomise_sum_up_v3.Rmd}"
: "${RANALYSE_CREATION_SCRIPT_SWE:=${SCRIPT_DIR}/SWE_sum_up_v1.5.2.Rmd}"
: "${MATRIX_CREATION_SCRIPT_SWE:=${SCRIPT_DIR}/create_matrix_VLADlov_swe_alpha2.py}"
: "${MATRIX_CREATION_SCRIPT:=${SCRIPT_DIR}/create_matrix_randoMRSI_beta6.py}"
: "${SNAPSHOT_CREATION_SCRIPT:=${SCRIPT_DIR}/snapshot_results_randomise_beta5.py}"

configure_python

cd ${GLOBAL_DIR}

type_images=$(echo "$type_images" | tr '[:upper:]' '[:lower:]')

case $type_images in
mrsi)
    # Sourcing
    source config_mrsi.sh
    # Making the values
    nii_cards=${NII_DIR_MRSI}
    nii_cards_orga=${NII_DIR_MRSI_orga}
    if [[ ${NII_DIR_MRSI_compression} == "yes" || ${NII_DIR_MRSI_compression} == "y" ]]; then
        COMPRESSION=".gz"
    else
        COMPRESSION=""
    fi

    SPACE_LABEL_SETTING=${MRSI_SPACE_LABEL:-mni}
    EXTRA_TAGS_SETTING=${MRSI_EXTRA_TAGS:-}

    data_type="mrsi" #to add to config
    acquisition="signal_filtbiharmonic_pvcorr"

    # NORMALLY WILL BE QUICKLY USELESS
    QMASKS=(Qmask QMask qmask qMask) # To change and to add to config but for the moment it will do the job

    # for QQ in ${QMASKS[@]}; do
    #     if [[ " ${MRSI_FILES_VALIDATED[@]} " =~ " ${QQ} " ]]; then
    #         qmask_val="$QQ"
    #     fi
    # done
    qmask_val="QMask"

    # B0-specific metabolite setup
    SUM_METAB_LABEL="SumMetabs"
    METAB_ACTIVE=("${METAB[@]}")
    METABSUM_ACTIVE=("${METABSUM[@]}")
    METABQUOTIENTS_ACTIVE=("${METABQUOTIENTS[@]}")

    case "${B0_MODE}" in
    7)
        SUM_METAB_LABEL="SumMetabs9"
        if [[ ${#METABSUM9[@]} -gt 0 ]]; then
            METABSUM_ACTIVE=("${METABSUM9[@]}")
        fi
        ;;
    7as3)
        SUM_METAB_LABEL="SumMetabs5"
        METAB_ACTIVE=("NAANAAG" "CrPCr" "Ins" "GPCPCh" "GluGln")
        if [[ ${#METABSUM5[@]} -gt 0 ]]; then
            METABSUM_ACTIVE=("${METABSUM5[@]}")
        fi
        METABQUOTIENTS_ACTIVE=()
        declare -A seen_metabquotients=()
        for ((i = 0; i < ${#METAB_ACTIVE[@]}; i++)); do
            for ((j = i + 1; j < ${#METAB_ACTIVE[@]}; j++)); do
                numerator=${METAB_ACTIVE[i]}
                denominator=${METAB_ACTIVE[j]}
                if [[ ${numerator} == "CrPCr" || ${denominator} == "CrPCr" ]]; then
                    numerator=$([[ ${numerator} == "CrPCr" ]] && echo "${denominator}" || echo "${numerator}")
                    denominator="CrPCr"
                fi
                key="${numerator}on${denominator}"
                if [[ -z "${seen_metabquotients[$key]}" ]]; then
                    METABQUOTIENTS_ACTIVE+=("${key}")
                    seen_metabquotients[$key]=1
                fi
            done
        done
        ;;
    esac

    METAB=("${METAB_ACTIVE[@]}")
    METABSUM=("${METABSUM_ACTIVE[@]}")
    METABQUOTIENTS=("${METABQUOTIENTS_ACTIVE[@]}")

    # NORMALLY USELESS NOW 
    # CONCS=(Conc conc)
    # for CC in ${CONCS[@]}; do
    #     if [[ " ${MRSI_FILES_VALIDATED[@]} " =~ " ${CC} " ]]; then
    #         conc_val="$CC"
    #     fi
    # done

    #PREPARING WHAT WHICH METABOLITES WE NEED TO DO
    # Initialize the metabolites to do
    TODO=()
    Newmetabs=()
    if [[ ${METABprompted} == "none" ]]; then
        echo "No absolute concentration of metabolites asked"
    elif [[ -n ${METABprompted} ]]; then
        # Loop through the first array (METABprompted)
        for item in "${METABprompted[@]}"; do
            # Check if the item exists in the second array (METAB)
            if [[ " ${METAB[@]} " =~ " ${item} " ]]; then
                # If the item is in both arrays, add it to METABTODO
                TODO+=("$item")
                Newmetabs+=("$item")
            else
                echo "${item} is not a recognized metabolites, it won't be done, sorry"
            fi
        done
    else
        TODO+=(${METAB[@]})
    fi
    if [[ -n ${Newmetabs} ]]; then
        METAB=(${Newmetabs[@]})
    fi

    if [[ ${SUM} == true && -n ${METABSUMprompted} ]]; then
        for item in "${METABSUMprompted[@]}"; do
            # Check if the item exists in the second array (METAB)
            if [[ " ${METABSUM[@]} " =~ " ${item} " ]]; then
                # If the item is in both arrays, add it to METABTODO
                TODO+=("$item")
            else
                echo "${item} is not a recognized metabolites, it won't be done, sorry"
            fi
        done
    elif [[ ${SUM} == true && -z ${METABSUMprompted} ]]; then
        TODO+=(${METABSUM[@]})
    fi

    if [[ ${QUOTIENTS} == true && -n ${METABQUOTIENTSprompted} ]]; then
        declare -A seen_quotients=()
        for item in "${METABQUOTIENTSprompted[@]}"; do
            matched_pattern=false
            for quotient in "${METABQUOTIENTS[@]}"; do
                if [[ "${quotient}" == ${item} ]]; then
                    matched_pattern=true
                    if [[ -z "${seen_quotients[$quotient]}" ]]; then
                        TODO+=("${quotient}")
                        seen_quotients[$quotient]=1
                    fi
                fi
            done
            if [[ ${matched_pattern} == false ]]; then
                echo "${item} is not a recognized metabolite quotient, it won't be done, sorry"
            fi
        done
    elif [[ ${QUOTIENTS} == true && -z ${METABQUOTIENTSprompted} ]]; then
        TODO+=(${METABQUOTIENTS[@]})
    fi

    # TO BE QUICLY REMOVED 
    # if [[ -z ${MODALITYprompted} ]]; then
    #     if [[ ${CONFIRMATION} == true ]]; then
    #         acquisition="signal_filtbiharmonic"
    #     else

    #         if [[ " ${MRSI_FILES_VALIDATED[@]} " =~ " ${MODALITY} " ]]; then
    #             acquisition=${MODALITY}
    #         else
    #             echo "${MODALITY} not found in the validated files for MRSI, please specify a valid file to work on after the argument --cards"
    #             exit 1
    #         fi
    #     fi
    # else
    #     if [[ " ${MRSI_FILES_VALIDATED[@]} " =~ " ${MODALITYprompted} " ]]; then
    #         acquisition=${MODALITYprompted}
    #     else
    #         echo "${MODALITYprompted} not found in the validated files for MRSI, please specify a valid file to work on after the argument --cards"
    #         exit 1
    #     fi
    # fi

    ;;
dti)
    source config_dti.sh
    nii_cards=${NII_DIR_DTI}
    nii_cards_orga=${NII_DIR_DTI_orga}
    if [[ ${NII_DIR_DTI_compression} == "yes" || ${NII_DIR_DTI_compression} == "y" ]]; then
        COMPRESSION=".gz"
    else
        COMPRESSION=""
    fi

    SPACE_LABEL_SETTING=$(trim_whitespace "${DTI_SPACE_LABEL:-mni}")
    EXTRA_TAGS_SETTING=$(trim_whitespace "${DTI_EXTRA_TAGS:-}")
    local dti_data_suffix
    dti_data_suffix=$(trim_whitespace "${DTI_DATA_SUFFIX:-dwi}")
    local dti_acq_label
    dti_acq_label=$(trim_whitespace "${DTI_ACQ_LABEL:-dti}")

    data_type="${dti_data_suffix}"
    acquisition="${dti_acq_label}"
    SMOOTHINGCARDS=true

    if [[ -z ${DESCRIPTION} ]]; then
        TODO=(${DTI_FILES_VALIDATED[@]})
    else
        for item in "${DESCRIPTION[@]}"; do
            # Check if the item exists in the second array (METAB)
            if [[ " ${DTI_FILES_VALIDATED[@]} " =~ " ${item} " ]]; then
                # If the item is in both arrays, add it to METABTODO
                TODO+=("$item")
            else
                echo "${item} was not found in the validated files you done with setup_VLAD.sh. It won't be done"

            fi
        done
    fi

    if [[ $SUM == true || $QUOTIENTS == true ]]; then
        echo "We are in DTI exploration, sum or quotients are not in the line, this is disabled"
        SUM=false
        QUOTIENTS=false
    fi

    ;;
structural)
    source config_structural.sh
    nii_cards=${NII_DIR_STRUCTURAL}
    nii_cards_orga=${NII_DIR_STRUCTURAL_orga}
    if [[ ${NII_DIR_STRUCTURAL_compression} == "yes" || ${NII_DIR_STRUCTURAL_compression} == "y" ]]; then
        COMPRESSION=".gz"
    else
        COMPRESSION=""
    fi

    SPACE_LABEL_SETTING=$(trim_whitespace "${STRUCTURAL_SPACE_LABEL:-mni}")
    EXTRA_TAGS_SETTING=$(trim_whitespace "${STRUCTURAL_EXTRA_TAGS:-}")
    local struct_acq_label
    struct_acq_label=$(trim_whitespace "${STRUCTURAL_ACQ_LABEL:-memprage}")
    local struct_data_suffix
    struct_data_suffix=$(trim_whitespace "${STRUCTURAL_DATA_SUFFIX:-T1w}")

    data_type="${struct_data_suffix}"
    acquisition="${struct_acq_label}"

    if [[ -z ${DESCRIPTION} ]]; then
        TODO=(${STRUCTURAL_FILES_VALIDATED[@]})
    else
        for item in "${DESCRIPTION[@]}"; do
            # Check if the item exists in the second array (METAB)
            if [[ " ${STRUCTURAL_FILES_VALIDATED[@]} " =~ " ${item} " ]]; then
                # If the item is in both arrays, add it to METABTODO
                TODO+=("$item")
            else
                echo "${item} was not found in the validated files you done with setup_VLAD.sh. It won't be done"

            fi
        done
    fi

    if [[ $SUM == true || $QUOTIENTS == true ]]; then
        echo "We are in structural exploration, sum or quotients are not in the line, this is disabled"
        SUM=false
        QUOTIENTS=false
    fi

    ;;
*)
    echo "Invalid image type for VLAD. 3 possible types : MRSI, DTI or structural (case insensitive)"
    exit 1
    ;;
esac
############# SUM UP of what VLAD will do #####################

echo "----------------------------------------------------"
echo "Cohort : $COHORT"
echo "Main path : ${GLOBAL_DIR}"
echo "What we are doing : $type_images"
echo "What will be analyzed : ${TODO[@]}"

if [[ ${type_images} == "mrsi" ]]; then
    echo "Type of files we are dealing with : $acquisition"
fi
WHATTODO=()
if [[ ${PREPARE} == true ]]; then
    WHATTODO+=("Matrix, 4d cards; ")
fi
if [[ $RANDOMISING == true ]]; then
    WHATTODO+=("Randomise; ")
fi
if [[ $EXTRACTING == true ]]; then
    WHATTODO+=("Extraction of significative results; ")
fi
if [[ $SUMUP == true ]]; then
    WHATTODO+=("Sum-up with R markdown; ")
fi
echo "What will be done : ${WHATTODO[@]}"
echo "Number of permutations : $NBPERMUT"
echo "Regions of interest : ${MASKS[@]}"
echo "----------------------------------------------------"

#############################################################
################# MATRIX DOING and checking #################
#############################################################
if [[ -z $NAME ]]; then
    echo "No name provided for analysis (4D card)"
    display_help
    exit 1
fi

check_names() {
    #Check if the NAME and MATRIX don't have an underscore (bugs with R) ----------------

    if [[ "$NAME" == *_* ]]; then
        echo "Warning: The name '$NAME' contains an underscore, which may cause a bug."
        echo "Proposing to change the underscore to a dash..."

        # Substitute underscore with dash
        NAME_MODIFIED="${NAME//_/-}"
        echo "The modified name is: $NAME_MODIFIED"

        # Optionally, you can prompt the user to accept the change or not
        if [[ $CONFIRMATION == true || $BATCH_WORKER == true ]]; then
            NAME=$NAME_MODIFIED
            echo "Matrix name changed to: $NAME"
        else

            read -p "Do you want to apply this change? (y/n) " answer
            case $answer in
            [Yy]*)
                NAME=$NAME_MODIFIED
                echo "Matrix name changed to: $NAME"
                ;;
            [Nn]*)
                echo "Matrix name remains unchanged: $NAME. Bugs may appear in the sum-up of the analyses."
                ;;
            *)
                echo "Invalid response. Matrix name remains unchanged: $NAME. Bugs may appear in the sum-up of the analyses."
                ;;
            esac
        fi
    fi

    if [[ "$MATRIX" == *_* ]]; then
        echo "Warning: The name '$MATRIX' contains an underscore, which may cause a bug."
        echo "Proposing to change the underscore to a dash..."

        # Substitute underscore with dash
        MATRIX_MODIFIED="${MATRIX//_/-}"
        echo "The modified name is: $MATRIX_MODIFIED"

        # Optionally, you can prompt the user to accept the change or not
        if [[ $CONFIRMATION == true || $BATCH_WORKER == true ]]; then
            MATRIX=$MATRIX_MODIFIED
            echo "Matrix name changed to: $MATRIX"
        else

            read -p "Do you want to apply this change? (y/n) " answer
            case $answer in
            [Yy]*)
                MATRIX=$MATRIX_MODIFIED
                echo "Matrix name changed to: $MATRIX"
                ;;
            [Nn]*)
                echo "Matrix name remains unchanged: $MATRIX. Bugs may appear in the sum-up of the analyses."
                ;;
            *)
                echo "Invalid response. Matrix name remains unchanged: $MATRIX. Bugs may appear in the sum-up of the analyses."
                ;;
            esac
        fi
    fi
}

if [[ ${REMAKEALL} == true ]]; then
    cd ${GLOBAL_DIR}/Cartes4D
    rm -rf ${NAME}
    cd ${GLOBAL_DIR}/Results
    rm -rf ${NAME}
    cd ${GLOBAL_DIR}/Listes
    rm List_${NAME}.txt
fi

domatrix() {
    # Check if the list of subjects is here and create one if needed ---------------------------
    cd ${GLOBAL_DIR}

    if [[ -e "Listes/List_${NAME}.txt" ]]; then
        SUBJECTS=($(cat ${GLOBAL_DIR}/Listes/List_${NAME}.txt))
    elif [[ ! -e "Listes/List_${NAME}.txt" && $CONFIRMATION == false ]]; then
        echo "The list of subjects wasn't find, let's create it. If you want to stop the process type exit, anything else will pursue the process"
        read STOP
        if [[ ${STOP} == "exit" ]]; then
            exit 0
        else
            echo "We will do the list then, just wait a bit :-)"
            DOLIST=true
            #Let's assume that if the list isn't here, the different files aren't here either
            mkdir -p ${GLOBAL_DIR}/Cartes4D/${NAME}
            mkdir -p ${GLOBAL_DIR}/Results/${NAME}
        fi
    else
        echo "No list found and we are in confirmation mode... Exiting"
        exit 1

    fi

    # Matrix creation (infinite loop until matrix is made with the python script) ---------------------
    if [[ $CONFIRMATION == false ]]; then

        while true; do
            if [[ -n $MATRIX && -e "${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.mat" ]]; then
                echo "Matrix ${MATRIX} found. Do you want to continue with it ? If n it will be deleted. Type \"exit\" to stop. (y, n, or exit)"
                read MATRIXRM

                if [[ ${MATRIXRM} == "y" ]]; then
                    echo "Let's go ! "
                    break
                elif [[ ${MATRIXRM} == "n" ]]; then
                    echo "Let's delete old files then"
                    cd ${GLOBAL_DIR}/Results/${NAME}
                    rm ${MATRIX}.csv
                    rm ${MATRIX}.mat
                    rm ${MATRIX}.con
                    rm -rf ${MATRIX}.fts
                    rm ${MATRIX}_visualisation.png
                    rm main_variable_${MATRIX}.txt
                elif [[ ${MATRIXRM} == "exit" ]]; then
                    echo "Goodbye"
                    exit 0
                else
                    echo "Not an answer that was expected"
                fi

            elif [[ -n $MATRIX && ! -e ${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.mat ]]; then
                echo "No matrix found. Do you want to create one ? y for yes, n for no"
                read DOMATRIX
                if [[ ${DOMATRIX} == "y" ]]; then

                    if [[ ${DOLIST} == true ]]; then
                        if [[ ${DIFFLIPID} == true ]]; then
                            echo "Don't forget to correct in your matrix for the lipid modality !"
                        fi
                        if [[ ${LONGITUDINAL} == true ]] && [[ ${LONGITUDINAL_TOOL} == "swe" ]]; then
                            "${VLAD_PYTHON_BIN}" "${MATRIX_CREATION_SCRIPT_SWE}" "${NAME}" "${MATRIX}" "${GLOBAL_DIR}" 1
                        else
                            "${VLAD_PYTHON_BIN}" "${MATRIX_CREATION_SCRIPT}" "${NAME}" "${MATRIX}" "${GLOBAL_DIR}" 1 "${ANOVA}"
                        fi
                        status=$?
                        wait
                        SUBJECTS=($(cat ${GLOBAL_DIR}/Listes/List_${NAME}.txt))
                        DO4D=true
                    else
                        if [[ ${DIFFLIPID} == true ]]; then
                            echo "Don't forget to correct in your matrix for the lipid modality !"
                        fi
                        if [[ ${LONGITUDINAL} == true ]] && [[ ${LONGITUDINAL_TOOL} == "swe" ]]; then
                            "${VLAD_PYTHON_BIN}" "${MATRIX_CREATION_SCRIPT_SWE}" "${NAME}" "${MATRIX}" "${GLOBAL_DIR}" 1
                        else
                            "${VLAD_PYTHON_BIN}" "${MATRIX_CREATION_SCRIPT}" "${NAME}" "${MATRIX}" "${GLOBAL_DIR}" 0 "${ANOVA}"
                        fi
                        status=$?
                    fi

                    if [[ $status -ne 0 || ! -e ${GLOBAL_DIR}/Results/${NAME}/${MATRIX}_mat.txt ]]; then
                        echo "The creation of the Matrix seems to have failed. The Matrix needs to exist. Type anything to restart, or type exit to stop the script"
                        read EXITMATRIX
                        if [[ $EXITMATRIX == "exit" ]]; then
                            echo "Ok bye !"
                            exit 0
                        else
                            echo "Let's start again"
                        fi
                    else

                        echo "Formating the files generated by the Python files, since randomise is a little bit temperamental... "
                        cd ${GLOBAL_DIR}/Results/${NAME}

                        Text2Vest ${MATRIX}_mat.txt ${MATRIX}.mat
                        Text2Vest ${MATRIX}_con.txt ${MATRIX}.con
                        if [[ ${LONGITUDINAL} == true && ${LONGITUDINAL_TOOL} == "swe" ]]; then
                           Text2Vest ${MATRIX}_design_sub.txt ${MATRIX}.sub
                        fi
                        

                        # Now that is seems to work, no need to keep this temporary files
                        rm ${MATRIX}_mat.txt
                        rm ${MATRIX}_con.txt
                        rm ${MATRIX}_contrast.png
                        rm ${MATRIX}_heatmap.png
                        # Attention : volontaire de ne pas supprimer le .txt de design_sub.txt car sert plus tard à R 
                        echo "Matrix ${MATRIX} done"
                        break
                    fi
                elif [[ ${DOMATRIX} == "n" ]]; then
                    echo "Goodbye !"
                    exit 0
                else
                    echo "It's y for yes or n for no, no other option possible"
                fi
            else
                echo "Please, provide a name for the Matrix :-)"
                read MATRIX
            fi
        done
    elif [[ ! -e "${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.mat" && $CONFIRMATION == true ]]; then
        echo "No Matrix found and we are in confirmation mode"
        exit 1
    else
        echo "Matrix found, we are good to go"
    fi
}

checkmatrix_4d() {

    local CARD4Dtocheck=$1

    check4D=$(fslval "${GLOBAL_DIR}/Cartes4D/${NAME}/${CARD4Dtocheck}" dim4)
    value=$(grep '/NumPoints' ${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.mat | awk '{print $2}')
    #echo "$value"

    if [[ ${check4D} -ne ${value} ]]; then
        echo "There seems to be a problem : your 4D card has ${check4D} subjects and your matrix ${value} subjects. VLAD and espacially Randomise cannot do an analysis like this."
        echo "You must relaunch VLAD with anoter population name (after the -n argument) so a new 4D card, fitting your desired matrix will be done."
        echo "The problem probably resides in missing values in your database. More / less missing values with this analysis than the previous one."
        echo "If you are very nice to the programmer of this shit, he will maybe do the relaunch of VLAD automatic in the next version ;-)"
        #echo "Deleting the matrix you just done"
        exit 0
        # cd ${GLOBAL_DIR}/Results/${NAME} -> we will check if this works before deleting all haha
        # rm ${MATRIX}.csv
        # rm ${MATRIX}.mat
        # rm ${MATRIX}.con
        # rm -rf ${MATRIX}.fts
        # rm ${MATRIX}_visualisation.png
        # rm main_variable_${MATRIX}.txt
    else
        echo "File 4D and Matrix are compatible, let's proceed"
    fi

}

##############################################################
# Functions to create 4d cards -------------------------------
##############################################################



build_space_suffix() {
    local space_label
    space_label=$(trim_whitespace "${1:-mni}")
    local extra_tags
    extra_tags=$(trim_whitespace "${2:-}")
    local suffix="_space-${space_label}"

    if [[ -n "$extra_tags" ]]; then
        for tag in $extra_tags; do
            suffix+="_${tag}"
        done
    fi

    echo "$suffix"
}

name_subject_file() {
    local subject=$1
    local visit=$2
    local data_type=$3
    local acquisition=$4
    local description=$5
    local compressed=$6
    local space_label=${7:-${SPACE_LABEL_SETTING:-mni}}
    local extra_tags=${8:-${EXTRA_TAGS_SETTING:-}}
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
        return 1
    fi

    local tag_suffix
    tag_suffix=$(build_space_suffix "$space_label" "$extra_tags")

    echo "sub-${subject}_ses-${visit}${tag_suffix}_acq-${acquisition}_desc-${description}_${data_type}.nii${compressed}"

}

name_subject_file_mrsi() { # ADDED FOR THE NEW BIDS
    local subject=$1
    local visit=$2
    local data_type=$3
    local met=$4
    local description=$5
    local compressed=$6
    subject=$(trim_whitespace "$subject")
    visit=$(trim_whitespace "$visit")
    data_type=$(trim_whitespace "$data_type")
    met=$(trim_whitespace "$met")
    description=$(trim_whitespace "$description")
    compressed=$(trim_whitespace "$compressed")

    if [[ $# -ne 6 ]]; then
        echo "name_subject_file badly used, this will cause some errors. Exiting"
        return 1
    fi

    local space_label=${SPACE_LABEL_SETTING:-mni}
    local extra_tags=${EXTRA_TAGS_SETTING:-}
    local tag_suffix
    tag_suffix=$(build_space_suffix "$space_label" "$extra_tags")

    echo "sub-${subject}_ses-${visit}${tag_suffix}_met-${met}_desc-${description}_${data_type}.nii${compressed}"

}

name_4d_file() {
    local data_type=$1
    local acquisition=$2
    local description=$3
    local name=$4
    local space_label=${5:-${SPACE_LABEL_SETTING:-mni}}
    local extra_tags=${6:-${EXTRA_TAGS_SETTING:-}}
    data_type=$(trim_whitespace "$data_type")
    acquisition=$(trim_whitespace "$acquisition")
    description=$(trim_whitespace "$description")
    name=$(trim_whitespace "$name")
    space_label=$(trim_whitespace "$space_label")
    extra_tags=$(trim_whitespace "$extra_tags")

    if [[ $# -lt 4 || $# -gt 6 ]]; then
        echo "name_4d_file badly used, this will cause some errors. Exiting"
        return 1
    fi

    local tag_suffix
    tag_suffix=$(build_space_suffix "$space_label" "$extra_tags")

    echo "4D_${name}${tag_suffix}_acq-${acquisition}_desc-${description}_${data_type}.nii.gz"

}

name_4d_file_mrsi() {  # ADDED FOR THE NEW BIDS
    local data_type=$1
    local met=$2
    local description=$3
    local name=$4
    local space_label=${5:-${SPACE_LABEL_SETTING:-mni}}
    local extra_tags=${6:-${EXTRA_TAGS_SETTING:-}}
    data_type=$(trim_whitespace "$data_type")
    met=$(trim_whitespace "$met")
    description=$(trim_whitespace "$description")
    name=$(trim_whitespace "$name")
    space_label=$(trim_whitespace "$space_label")
    extra_tags=$(trim_whitespace "$extra_tags")

    if [[ $# -lt 4 || $# -gt 6 ]]; then
        echo "name_4d_file badly used, this will cause some errors. Exiting"
        return 1
    fi

    local tag_suffix
    tag_suffix=$(build_space_suffix "$space_label" "$extra_tags")

    echo "4D_${name}${tag_suffix}_met-${met}_desc-${description}_${data_type}.nii.gz"

}

resolve_analysis_4d_cards() {
    local met="$1"

    if [[ ${type_images} == "mrsi" ]]; then
        OrigCARD4D=$(name_4d_file_mrsi "${data_type}" "${met}" "${acquisition}" "${NAME}") || return 1
        CARD4D=$(name_4d_file_mrsi "${data_type}" "${met}" "${acquisition}-temp" "${NAME}") || return 1
    elif [[ ${SMOOTHINGCARDS} == false ]]; then
        OrigCARD4D=$(name_4d_file "${data_type}" "${acquisition}" "${met}" "${NAME}") || return 1
        CARD4D=$(name_4d_file "${data_type}" "${acquisition}-temp" "${met}" "${NAME}") || return 1
    else
        OrigCARD4D=$(name_4d_file "${data_type}" "${acquisition}" "${met}-smooth${SMOOTHSIGMA}" "${NAME}") || return 1
        CARD4D=$(name_4d_file "${data_type}" "${acquisition}-temp" "${met}-smooth${SMOOTHSIGMA}" "${NAME}") || return 1
    fi
}

select_analysis_4d_card() {
    local met="$1"
    local card4d_path="${GLOBAL_DIR}/Cartes4D/${NAME}"

    if ! resolve_analysis_4d_cards "${met}"; then
        echo "Unable to determine the 4D card names for ${met}."
        return 1
    fi

    if [[ -f "${card4d_path}/${CARD4D}" ]]; then
        CARD4Dtoanalyze="${card4d_path}/${CARD4D}"
    elif [[ -f "${card4d_path}/${OrigCARD4D}" ]]; then
        CARD4Dtoanalyze="${card4d_path}/${OrigCARD4D}"
        echo "Masked temporary 4D card not found for ${met}; using original card: ${CARD4Dtoanalyze}"
    else
        echo "No 4D card found for ${met}."
        echo "Expected temporary card: ${card4d_path}/${CARD4D}"
        echo "Expected original card: ${card4d_path}/${OrigCARD4D}"
        return 1
    fi
}

strip_ratio_suffix() {
    local met="$1"
    if [[ "$met" == *"RatioSum"* ]]; then
        echo "${met%%RatioSum*}"
    else
        echo "$met"
    fi
}

compare_nifti_files() {
    file1="$1"
    file2="$2"

    # Extract dimensions for file1
    dim1_file1=$(fslval "$file1" dim1)
    dim2_file1=$(fslval "$file1" dim2)
    dim3_file1=$(fslval "$file1" dim3)

    # Extract dimensions for file2
    dim1_file2=$(fslval "$file2" dim1)
    dim2_file2=$(fslval "$file2" dim2)
    dim3_file2=$(fslval "$file2" dim3)

    # Extract pixel dimensions for file1
    pixdim1_file1=$(fslval "$file1" pixdim1)
    pixdim2_file1=$(fslval "$file1" pixdim2)
    pixdim3_file1=$(fslval "$file1" pixdim3)

    # Extract pixel dimensions for file2
    pixdim1_file2=$(fslval "$file2" pixdim1)
    pixdim2_file2=$(fslval "$file2" pixdim2)
    pixdim3_file2=$(fslval "$file2" pixdim3)

    # Compare dimensions and pixel size
    if [[ "$dim1_file1" == "$dim1_file2" && "$dim2_file1" == "$dim2_file2" && "$dim3_file1" == "$dim3_file2" &&
        "$pixdim1_file1" == "$pixdim1_file2" && "$pixdim2_file1" == "$pixdim2_file2" && "$pixdim3_file1" == "$pixdim3_file2" ]]; then
        echo 0 # Both dimensions and pixel sizes match
    else
        echo 1 # Either dimensions or pixel sizes differ
    fi
}

reslice_mask() {
    local filetocheck="$1"
    local fileref="$2"

    if [[ -z "$filetocheck" || ! -f "$filetocheck" ]]; then
        echo "Mask $filetocheck not found for reslicing."
        return 1
    fi

    local comparison
    comparison=$(compare_nifti_files "${filetocheck}" "${fileref}")

    if [[ ${comparison} -eq 1 ]]; then
        local mask_dir
        mask_dir=$(dirname "$filetocheck")
        local mask_name
        mask_name=$(basename "$filetocheck")
        local backup_dir="${mask_dir}/originalmasks"
        mkdir -p "$backup_dir"
        local originfile="${backup_dir}/${mask_name}"
        cp "${filetocheck}" "${originfile}"
        flirt -in "${filetocheck}" -ref "${fileref}" -o "${filetocheck}" -usesqform -dof 6 -applyxfm
        if [[ $? -eq 0 ]]; then
            echo "File has been flirted. Original file is stored in ${backup_dir}."
        else
            return 1
        fi
    fi

}

get_mask_directory_for_modality() {
    local modality="${1:-${type_images}}"
    local candidates=()

    if [[ -n "${GLOBAL_DIR}" ]]; then
        if [[ -n "$COHORT" && -n "$modality" ]]; then
            candidates+=("${GLOBAL_DIR}/Masques/${COHORT}/${modality}")
        fi
        if [[ -n "$COHORT" ]]; then
            candidates+=("${GLOBAL_DIR}/Masques/${COHORT}")
        fi
        if [[ -n "$modality" ]]; then
            candidates+=("${GLOBAL_DIR}/Masques/${modality}")
        fi
        candidates+=("${GLOBAL_DIR}/Masques")
    fi

    for dir in "${candidates[@]}"; do
        if [[ -n "$dir" && -d "$dir" ]]; then
            echo "$dir"
            return 0
        fi
    done

    echo "${GLOBAL_DIR}/Masques"
}

get_mask_file_path() {
    local mask_filename="$1"
    local modality="${2:-${type_images}}"
    if [[ -z "$mask_filename" ]]; then
        return 1
    fi

    local search_locations=()
    local primary_dir
    primary_dir=$(get_mask_directory_for_modality "$modality")
    if [[ -n "$primary_dir" ]]; then
        search_locations+=("${primary_dir}/${mask_filename}")
    fi
    if [[ -n "$COHORT" ]]; then
        search_locations+=("${GLOBAL_DIR}/Masques/${COHORT}/${mask_filename}")
    fi
    if [[ -n "$modality" ]]; then
        search_locations+=("${GLOBAL_DIR}/Masques/${modality}/${mask_filename}")
    fi
    search_locations+=("${GLOBAL_DIR}/Masques/${mask_filename}")

    for candidate in "${search_locations[@]}"; do
        if [[ -f "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done

    if [[ -d "${GLOBAL_DIR}/Masques" ]]; then
        local fallback
        fallback=$(find "${GLOBAL_DIR}/Masques" -type f -name "$mask_filename" 2>/dev/null | head -n 1)
        if [[ -n "$fallback" ]]; then
            echo "$fallback"
            return 0
        fi
    fi

    return 1
}

wrap4dcards() { # This needs to be more flexible...

    TARGET_DIR="${GLOBAL_DIR}/Cartes4D/${NAME}"
    local data_type=$1
    local acquisition=$2
    local cardtodo=$3

    #for cardtodo in ${TODO[@]}; do
    echo "Creating 4D card for $cardtodo"

    mkdir -p $TARGET_DIR

    for SUBJ in ${SUBJECTS[@]}; do
        sub=${SUBJ%_*}
        visit=${SUBJ#*_}

        if [[ ${type_images} == "mrsi" ]]; then
            file_subject=$(name_subject_file_mrsi "${sub}" "${visit}" "${data_type}" "${cardtodo}" "${acquisition}" "${COMPRESSION}")
        else
            file_subject=$(name_subject_file "${sub}" "${visit}" "${data_type}" "${acquisition}" "${cardtodo}" "${COMPRESSION}")
        fi
        #file_subject=$(name_subject_file "${sub}" "${visit}" "${data_type}" "${acquisition}" "${cardtodo}" "${COMPRESSION}")
        if [[ $? -ne 0 ]]; then
            echo "An error occurred while running name_subject_file. Exiting script."
            exit 1 # Exit script if the function returns an error
        fi

        cd ${nii_cards}
        FILE_PATH=""
        FILE_PATH=$(find "${nii_cards}" -type f -name "$file_subject" 2>/dev/null | head -n 1)
        if [[ -z "$FILE_PATH" ]]; then
            echo "File for $SUBJ not found, exiting to avoid further errors"
            echo "Catching errors : sub : $sub ; visit : $visit ; data_type : $data_type ; acq : $acquisition ; todo : $cardtodo ; compr : $COMPRESSION "
            exit 1
        else
            cp "$FILE_PATH" "${TARGET_DIR}/${sub}-${visit}-${cardtodo}.nii${COMPRESSION}"
        fi
    done
    cd $TARGET_DIR

    if [[ ${type_images} == "mrsi" ]]; then
        card4dtodo=$(name_4d_file_mrsi "${data_type}" "${cardtodo}" "${acquisition}" "${NAME}")
    else
        card4dtodo=$(name_4d_file "${data_type}" "${acquisition}" "${cardtodo}" "${NAME}")
    fi
    #card4dtodo=$(name_4d_file "${data_type}" "${acquisition}" "${cardtodo}" "${NAME}")

    fslmerge -t ${card4dtodo} *-${cardtodo}.nii${COMPRESSION}

    if [[ ${SMOOTHINGCARDS} == true ]]; then
        cardtodoS="${cardtodo}-smooth${SMOOTHSIGMA}"
        card4dsmoothed=$(name_4d_file "${data_type}" "${acquisition}" "${cardtodoS}" "${NAME}")
        fslmaths ${card4dtodo} -s ${SMOOTHSIGMA} ${card4dsmoothed}
        rm -f ${card4dtodo}
    fi

    rm -f *-${cardtodo}.nii${COMPRESSION}
    #done

}

wrapmasksmrsi() {
    #### No need for reslicing anymore :-)

    ## DUE TO PROBLEMS : MAYBE WE ADD AN OPTION TO RESLICE THE HARVARD-OXFORD MASK TO A SPECIFIC MNI..? A very basic flirt.

    #Making 4D Qmasks here now
    TARGET_DIR="${GLOBAL_DIR}/Cartes4D/${NAME}"

    MetabQMask=(${METAB[@]})
    if [[ ${SUM} == true || ${QUOTIENTS} == true ]]; then
        MetabQMask+=("${SUM_METAB_LABEL}")
    fi

    for MET in ${MetabQMask[@]}; do
        qmask4dtodo=$(name_4d_file_mrsi "${data_type}" "${qmask_val}" "${MET}" "${NAME}")
        if [[ $? -ne 0 ]]; then
            echo "Error naming 4D file at the checking for 4D card"
            exit 1
        fi
        if [[ ! -e ${GLOBAL_DIR}/Cartes4D/${NAME}/${qmask4dtodo} ]]; then
            echo "Creating QMask for ${MET}"
            for SUBJ in ${SUBJECTS[@]}; do
                sub=${SUBJ%_*}
                visit=${SUBJ#*_}

                file_subject=$(name_subject_file_mrsi "${sub}" "${visit}" "${data_type}" "${MET}" "${qmask_val}" "${COMPRESSION}")
                if [[ $? -ne 0 ]]; then
                    echo "An error occurred while running name_subject_file. Exiting script."
                    exit 1 # Exit script if the function returns an error
                fi

                cd ${nii_cards}
                FILE_PATH=""
                FILE_PATH=$(find "${nii_cards}" -type f -name "$file_subject" 2>/dev/null | head -n 1)
                if [[ -z "$FILE_PATH" ]]; then
                    echo "File for $SUBJ not found, exiting to avoid further errors"
                    exit 1
                else
                    cp "$FILE_PATH" "${TARGET_DIR}/${sub}-${visit}-${qmask_val}-${MET}.nii${COMPRESSION}"
                fi
            done
            cd $TARGET_DIR

            card4dtodo=$(name_4d_file_mrsi "${data_type}" "${MET}" "${qmask_val}" "${NAME}")

            fslmerge -t ${card4dtodo} *-${qmask_val}-${MET}.nii${COMPRESSION}

            rm -f *-${qmask_val}-${MET}.nii${COMPRESSION}
        fi
    done

    # Making the global quali masks to analyze only on these portions
    echo "==== Global quality masks for MRSI + GM/WM for randomise ===="
    cd ${GLOBAL_DIR}

    mkdir -p Results/${NAME}
    cd Results/${NAME}

    for MET in ${MetabQMask[@]}; do

        #Creation d'un masque où >68% (ou selon le quality tresh decidé) des sujets ont des données
        QMASK4D=$(name_4d_file_mrsi ${data_type} ${MET} ${qmask_val} ${NAME})
        fslmaths ${GLOBAL_DIR}/Cartes4D/${NAME}/${QMASK4D} -Tmean ${MET}_QMask_mean
        fslmaths ${MET}_QMask_mean.nii.gz -thr 0.${QUALITRESH} -bin ${MET}_Qmask_thr${QUALITRESH}

        for MASK in ${MASKS[@]}; do
            local mask_filename="HarvardOxford-${MASK}.nii.gz"
            local mask_file
            mask_file=$(get_mask_file_path "$mask_filename" "mrsi")
            if [[ -z "$mask_file" ]]; then
                echo "Mask $mask_filename not found for cohort ${COHORT} (modality mrsi). Aborting."
                exit 1
            fi
            fslmaths "${mask_file}" -mul ${MET}_Qmask_thr${QUALITRESH} ${MET}_${NAME}mask${MASK}_quali
        done
        rm ${MET}_QMask_mean.nii.gz
        rm ${MET}_Qmask_thr${QUALITRESH}.nii.gz

    done

}

unique_masking() { # Must be adapted

    # MUST BE LAUNCHED ONLY IN RANDOEXTRACT, with MET, QUALIMASK, METQUOTIENT already declared !!
    #cd # ?

    if [[ ${METQUOTIENT} == false ]]; then
        #QMASK="${GLOBAL_DIR}/Cartes4D/${NAME}/${MET}/4D_${MET}_${NAME}_QMask.nii.gz"
        QMASK=$(name_4d_file_mrsi "${data_type}" "${met_quali}" "${qmask_val}" "${NAME}")
    else
        #QMASK="${GLOBAL_DIR}/Cartes4D/${NAME}/SumMetabs/4D_SumMetabs_${NAME}_QMask.nii.gz"
        QMASK=$(name_4d_file_mrsi "${data_type}" "${SUM_METAB_LABEL}" "${qmask_val}" "${NAME}")

    fi
    echo "Taking ${QMASK} for quality masking by subject"
    if [[ $? -ne 0 ]]; then
        echo "An error occurred while running name_subject_file. Exiting script."
        exit 1 # Exit script if the function returns an error
    fi

    DIR_CM="${GLOBAL_DIR}/Results/ConfirmationMasked/${NAME}_${MATRIX}_${MET}_${acquisition}_${MASK}/"
    mkdir -p $DIR_CM
    cd $DIR_CM
    # Making the inverted qmasks for masking the bad quality voxels for randomise
    echo "Setting up inverted masking"
    fslsplit ${CARD4D_path}/${QMASK} qmask_ -t
    NBVOL=$(fslnvols ${CARD4D_path}/${QMASK})

    for ((n = 0; n < ${NBVOL}; n++)); do

        padded_n=$(printf "%04d" $n)

        fslmaths qmask_${padded_n}.nii.gz -binv qmask_inv${padded_n} #We invert the qmasks

        fslmaths qmask_inv${padded_n}.nii.gz -mul ${QUALIMASK} qmask_Interest${padded_n}
    done

    # Finding how much column our original matrix has
    num_waves=$(head -n 1 ${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.con | cut -d ' ' -f 2)
    num_waves=$((num_waves + 1))

    #Making the setup for confirmation maskes of randomise and putting them into a folder
    setup_masks ${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.mat ${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.con ${MATRIX}-CM qmask_Interest*

    

    cp ${QUALIMASK} ${DIR_CM}

    # ALL OF THIS IS NORMALLY NOT NEEDED ANYMORE - stays here for the moment waiting to be deleted
    # qualimask_cm=$(basename "$QUALIMASK")

    # wrap4dcards ${data_type} ${conc_val} ${MET}

    # if [[ $? -eq 0 ]]; then
    #     conc_4d=$(name_4d_file "${data_type}" "${conc_val}" "${MET}" "${NAME}")
    # fi

    # cd ${DIR_CM}
    # touch command.txt

    # if [[ ${ANOVA} -eq 1 ]]; then
    #     complement_anova=" -f {MATRIX}.fts --fonly -D"
    # else
    #     complement_anova=""
    # fi

    # echo "randomise_parallel -i ${GLOBAL_DIR}/Cartes4D/${NAME}/${conc_4d} -o ${RESULT}_CM -d ${MATRIX}-CM.mat -t ${MATRIX}-CM.con --vxl=-${num_waves} --vxf=${MATRIX}-CM.nii.gz -m ${qualimask_cm} -n 5000 -T ${complement_anova}" >>command.txt

    rm qmask_*

}

#ANALYSE RESULTATS RANDOMISE
randoanalyze() {

    local MASK="$1"
    local MET="$2"
    local RESULT="$3"
    local CARD4Dtoanalyze="$4"

    if [[ ${ONLYORIGINAL} != true ]]; then
        if [[ -z "${CARD4Dtoanalyze}" ]]; then
            echo "No 4D card was provided for extracting ${MET}. Exiting."
            return 1
        fi
        if [[ ! -f "${CARD4Dtoanalyze}" ]]; then
            echo "The 4D card selected for extracting ${MET} does not exist: ${CARD4Dtoanalyze}"
            return 1
        fi
    fi

    METQUOTIENT=false
    for check in ${METABQUOTIENTS[@]}; do
        if [[ "$check" == ${MET} ]]; then
            METQUOTIENT=true
            break
        fi
    done

    met_quali=$(strip_ratio_suffix "${MET}")

    cd ${GLOBAL_DIR}/Results/${NAME}/${MET}/${MATRIX}
    CARD4D_path="${GLOBAL_DIR}/Cartes4D/${NAME}/"


    # if [[ ${SMOOTHINGCARDS} == false ]]; then
    #     if [[ ${data_type} == "mrsi" ]]; then
    #         CARD4Dtoanalyze=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}" "${NAME}")
    #     else
    #         CARD4Dtoanalyze=$(name_4d_file "${data_type}" "${acquisition}" "${MET}" "${NAME}")
    #     fi
    # else
    #     CARD4Dtoanalyze=$(name_4d_file "${data_type}" "${acquisition}" "${MET}-smooth${SMOOTHSIGMA}" "${NAME}")
    # fi

    # Finding how many contrasts have been done (use design files to avoid stale .nii/.nii.gz)
    if [[ ${ANOVA} -eq 0 ]]; then
        TEST="t"
        CONTRASTS=$(awk '/^\/NumPoints/ {print $2; exit}' "../../${MATRIX}.con")
    else
        TEST="f"
        if [[ -f "../../${MATRIX}.fts" ]]; then
            CONTRASTS=$(awk '/^\/NumContrasts/ {print $2; exit}' "../../${MATRIX}.fts")
        else
            CONTRASTS=$(awk '/^\/NumPoints/ {print $2; exit}' "../../${MATRIX}.con")
        fi
    fi
    if [[ -z "${CONTRASTS}" ]]; then
        echo "Could not determine number of contrasts for ${MATRIX}. Exiting."
        exit 1
    fi

    for ((i = 1; i <= ${CONTRASTS}; i++)); do
        cd ${GLOBAL_DIR}/Results/${NAME}/${MET}/${MATRIX}

        if [[ -e min_max.txt ]]; then
            rm min_max.txt
        fi

        #Which contrast are significative (FYI, results from randomise in corrp are in 1-p)
        stats_file_base="${RESULT}_tfce_corrp_${TEST}stat${i}"
        stats_file="${stats_file_base}.nii.gz"
        if [[ -f "${stats_file}" ]]; then
            stats_file="${stats_file}"
        elif [[ -f "${stats_file_base}.nii" ]]; then
            stats_file="${stats_file_base}.nii"
        else
            echo "Missing ${stats_file_base}.nii(.gz), skipping contrast ${i}"
            continue
        fi

        fslstats -t "${stats_file}" -R >>min_max.txt

        # min_max=($(cat min_max.txt))
        # max=${min_max[2]}
        read min max <min_max.txt
        #Extracting mean value from significative contrasts
        if (($(echo "$max > 0.95" | bc -l))); then
            fslmaths "${stats_file}" -thr 0.95 -bin thresh_mask_${acquisition}_${MASK}_contrast${i}
            #======Verify the name of the 4D card later======

            #Info in the sum-up for the analysis

            echo "####For ${MET}, the contrast ${i} in ${acquisition} in ${MASK} region is significative (p =< 0.05)####" >>../../Result_randomise_${NAME}_${MATRIX}.txt

            echo "####For ${MET}, the contrast ${i} in ${acquisition} in ${MASK} region is significative (p =< 0.05)####"
            echo "====Running data extraction + image making on this modality===="

            # Extract mean values only on voxels with good quality
            MEANTS="meants__${NAME}__${MET}__${MATRIX}__${acquisition}__${MASK}__contrast${i}.txt"
            if [[ ${ONLYORIGINAL} == true ]]; then

                if [[ -e "${MEANTS}" ]]; then
                    rm -f "${MEANTS}"
                    touch "${MEANTS}"
                else
                    touch "${MEANTS}"
                fi

                #We are going directly to the source now

                for SUBJ in "${SUBJECTS[@]}"; do

                    sub=${SUBJ%_*}
                    visit=${SUBJ#*_}

                    # FINDING FILES CONC AND QMASK TO EXTRACT
                    if ! file_subject_conc=$(name_subject_file_mrsi "${sub}" "${visit}" "${data_type}" "${MET}" "${acquisition}" "${COMPRESSION}"); then
                        echo "An error occurred while naming the concentration file for ${SUBJ}. Exiting script."
                        return 1
                    fi

                    if [[ ${METQUOTIENT} == false ]]; then
                        if ! file_subject_qmask=$(name_subject_file_mrsi "${sub}" "${visit}" "${data_type}" "${met_quali}" "${qmask_val}" "${COMPRESSION}"); then
                            echo "An error occurred while naming the quality-mask file for ${SUBJ}. Exiting script."
                            return 1
                        fi
                    else
                        if ! file_subject_qmask=$(name_subject_file_mrsi "${sub}" "${visit}" "${data_type}" "${SUM_METAB_LABEL}" "${qmask_val}" "${COMPRESSION}"); then
                            echo "An error occurred while naming the quality-mask file for ${SUBJ}. Exiting script."
                            return 1
                        fi
                    fi

                    cd "${nii_cards}" || return 1
                    FILE_PATH_conc=""
                    FILE_PATH_conc=$(find "${nii_cards}" -type f -name "$file_subject_conc" 2>/dev/null | head -n 1)
                    if [[ -z "$FILE_PATH_conc" ]]; then
                        echo "File conc for $SUBJ not found, exiting to avoid further errors"
                        return 1
                    fi

                    FILE_PATH_qmask=""
                    FILE_PATH_qmask=$(find "${nii_cards}" -type f -name "$file_subject_qmask" 2>/dev/null | head -n 1)
                    if [[ -z "$FILE_PATH_qmask" ]]; then
                        echo "File qmask for $SUBJ not found, exiting to avoid further errors"
                        return 1
                    fi

                    cd "${GLOBAL_DIR}/Results/${NAME}/${MET}/${MATRIX}" || return 1
                    temp_mask="${PWD}/threshed-${sub}-${visit}-${MET}-${i}.nii.gz"
                    temp_log="${PWD}/log-${sub}-${visit}-${MET}-${i}.nii.gz"

                    # MASKING
                    if ! fslmaths "${FILE_PATH_qmask}" -mul "thresh_mask_${acquisition}_${MASK}_contrast${i}" "${temp_mask}"; then
                        echo "Unable to create the subject quality mask for ${SUBJ}. Exiting."
                        rm -f "${temp_mask}" "${temp_log}"
                        return 1
                    fi

                    #EXTRACTING
                    if [[ ${LOGARITHM} == true ]]; then
                        if ! fslmaths "${FILE_PATH_conc}" -log "${temp_log}"; then
                            echo "Unable to create the logarithmic image for ${SUBJ}. Exiting."
                            rm -f "${temp_mask}" "${temp_log}"
                            return 1
                        fi
                        if ! mean=$(fslmeants -i "${temp_log}" -m "${temp_mask}"); then
                            echo "Unable to extract the logarithmic mean for ${SUBJ}. Exiting."
                            rm -f "${temp_mask}" "${temp_log}"
                            return 1
                        fi
                    else
                        if ! mean=$(fslmeants -i "${FILE_PATH_conc}" -m "${temp_mask}"); then
                            echo "Unable to extract the mean for ${SUBJ}. Exiting."
                            rm -f "${temp_mask}" "${temp_log}"
                            return 1
                        fi
                    fi

                    #On enregistre la valeur dans le fichier txt
                    echo "${mean}" >>"${MEANTS}"
                    rm -f "${temp_mask}" "${temp_log}"

                done

                echo "Meants extracted for quality voxels on clusters with p < 0.05 for ${MET} in contrast ${i} on modality ${SUFFIX} in ${MASK} region, only in \"original\" voxels" >>../../Result_randomise_${NAME}_${MATRIX}.txt

            else
                if [[ -e "${MEANTS}" ]]; then
                    rm -f "${MEANTS}"
                fi

                fslmeants -i "${CARD4Dtoanalyze}" -m "thresh_mask_${acquisition}_${MASK}_contrast${i}" -o "${MEANTS}"

                echo "Meants extracted on clusters with p < 0.05 for ${MET} in contrast ${i} on modality ${SUFFIX} in ${MASK} region" >>../../Result_randomise_${NAME}_${MATRIX}.txt
            fi

            # Add the path of the .txt file to the global file for R
            echo "${GLOBAL_DIR}/Results/${NAME}/${MET}/${MATRIX}/${MEANTS}" >>../../To_analyse_with_R_${NAME}_${MATRIX}.txt

            # Creating the image of this significative analysis
            PATH_IMAGE="${GLOBAL_DIR}/Results/${NAME}/${MET}/${MATRIX}/${stats_file}"
            NAME_IMAGE="${MET}__${MATRIX}__${acquisition}__${MASK}__contrast${i}.png"

            if (( i % 2 == 0 )); then
                colormap=2
            else
                colormap=1
            fi
            "${VLAD_PYTHON_BIN}" "${SNAPSHOT_CREATION_SCRIPT}" "${PATH_IMAGE}" "${NAME_IMAGE}" "${colormap}" "${T1_MNI}" # A METTRE A JOUR


        elif (($(echo "$max > 0.90" | bc -l))); then
            echo "####For ${MET}, the contrast ${i} in modality ${SUFFIX} in ${MASK} region is neer significance (p =< 0.1)####" >>../../Result_randomise_${NAME}_${MATRIX}.txt

            echo "####For ${MET}, the contrast ${i} in modality ${SUFFIX} in ${MASK} region is neer significance (p =< 0.1) ####"

        fi

    done


}

# Randomise itself + randomise initialising functions --------------------

randoextract() {

    # SUFFIX="$1"
    MASK="$1"
    MET="$2"
    RESULT="$3"

    # Checking for the suffix to see which metabolites we will to do here
    # if [[ ${SUFFIX} == "filled" || ${SUFFIX} == "basic" || ${SUFFIX} == "conc" ]]; then
    #     declare -a METABTODO=("${METABTOTAL[@]}")
    # elif [[ ${SUFFIX} == "RatioSum" ]]; then
    #     declare -a METABTODO=("${METAB[@]}")
    # else
    #     declare -a METABTODO=("${METAB[@]}")
    #     echo "WARNING : the 4D files are wierdly named, it is possible that the script will not work properly. Continuing nonetheless..."
    # fi

    #for MET in ${TODO[@]}; do # We are keeping MET for the moment but it will accomodates with the dti and structural
    echo "======================================================================================================================================"
    echo "===Randomise ${MET} using ${MATRIX} matrix TFCE ${NBPERMUT} times for ${acquisition} modality in ${MASK}===="
    echo "======================================================================================================================================"

    echo "Initializing..."

    #Checking if we are dealing with a quotient, in this case the QMask will be the same as the SumMetabs to make it more simple.
    METQUOTIENT=false
    for check in ${METABQUOTIENTS[@]}; do
        if [[ "$check" == ${MET} ]]; then
            METQUOTIENT=true
            break
        fi
    done

    met_quali=$(strip_ratio_suffix "${MET}")

    CARD4D_path="${GLOBAL_DIR}/Cartes4D/${NAME}"

    if [[ ${METQUOTIENT} == false && ${type_images} == "mrsi" ]]; then # SI MRSI et pas quotient
        QUALIMASK="${GLOBAL_DIR}/Results/${NAME}/${met_quali}_${NAME}mask${MASK}_quali.nii.gz"
        OrigCARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}" "${NAME}")
        if [[ $? -ne 0 ]]; then
            echo "An error occurred while running name_4d_file. Exiting script."
            exit 1 # Exit script if the function returns an error
        fi
        CARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}-temp" "${NAME}")
        #CARD4D_path="${GLOBAL_DIR}/Cartes4D/${NAME}/"
    elif [[ ${METQUOTIENT} == true && ${type_images} == "mrsi" ]]; then # SI MRSI et quotient -> utilisation du Qmask summetabs (sinon on s'en sort pas)
        QUALIMASK="${GLOBAL_DIR}/Results/${NAME}/${SUM_METAB_LABEL}_${NAME}mask${MASK}_quali.nii.gz"
        OrigCARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}" "${NAME}")
        if [[ $? -ne 0 ]]; then
            echo "An error occurred while running name_4d_file. Exiting script."
            exit 1 # Exit script if the function returns an error
        fi
        CARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}-temp" "${NAME}")

    elif [[ ${type_images} != "mrsi" ]]; then
        if [[ ${SMOOTHINGCARDS} == false ]]; then #SI pas de Smoothing

            OrigCARD4D=$(name_4d_file "${data_type}" "${acquisition}" "${MET}" "${NAME}")
            if [[ $? -ne 0 ]]; then
                echo "An error occurred while running name_4d_file. Exiting script."
                exit 1 # Exit script if the function returns an error
            fi
            CARD4D=$(name_4d_file "${data_type}" "${acquisition}-temp" "${MET}" "${NAME}")

        else # SI smoothing (mis automatiquement pour le DTI : valeur du smooth par défaut pour le DTI)
            OrigCARD4D=$(name_4d_file "${data_type}" "${acquisition}" "${MET}-smooth${SMOOTHSIGMA}" "${NAME}")
            if [[ $? -ne 0 ]]; then
                echo "An error occurred while running name_4d_file. Exiting script."
                exit 1 # Exit script if the function returns an error
            fi
            CARD4D=$(name_4d_file "${data_type}" "${acquisition}-temp" "${MET}-smooth${SMOOTHSIGMA}" "${NAME}")
        fi
        if [[ ${type_images} == "dti" ]]; then
            QUALIMASK=$(get_mask_file_path "FA_2500.nii.gz" "dti")
            if [[ -z "$QUALIMASK" ]]; then
                echo "Unable to locate FA_2500.nii.gz for DTI masks. Please prepare masks with Prep_VLAD_b5.sh."
                exit 1
            fi
        else
            local mask_file
            mask_file=$(get_mask_file_path "HarvardOxford-${MASK}.nii.gz" "${type_images}")
            if [[ -z "$mask_file" ]]; then
                echo "Unable to locate HarvardOxford-${MASK}.nii.gz for ${type_images}. Please prepare masks with Prep_VLAD_b5.sh."
                exit 1
            fi
            QUALIMASK="${mask_file}"
        fi

    else
        echo "I don't know where we are but there seems to be an error while loading files for Randomise"
        exit 1
    fi

    #cd $MET
    #Checking for quali QMask
    if [[ ! -e "${QUALIMASK}" && ${type_images} == "mrsi" ]]; then
        wrapmasksmrsi
    fi

    # Doing specific masking if required

    if [[ ${CONFIRMATION} == true && ${CONFIRMATION_MASKS} == true ]]; then
        if [[ -e ${MATRIX}-CM.mat && -e ${MATRIX}-CM.con && -e ${MATRIX}-CM.nii.gz ]]; then
            echo "Specific masking for subject and appropriate matrix found. Proceeding."
        else
            unique_masking
        fi
        num_waves=$(head -n 1 ${GLOBAL_DIR}/Results/${NAME}/${MATRIX}.con | cut -d ' ' -f 2)
        num_waves=$((num_waves + 1))
    fi

    #Checking for 4D card
    if [[ ! -f "${CARD4D_path}/${OrigCARD4D}" ]]; then
        echo "No 4D file found for Randomise, this is a bug. Exiting"
        exit 1
    fi

    #Creating a temp 4D file to make randomise lighter
    cd ${CARD4D_path}
    fslmaths ${CARD4D_path}/${OrigCARD4D} -mas ${QUALIMASK} ${CARD4D}
    
    if [[ ${LOGARITHM} == true ]]; 
    then
        echo "Applying logarithm to the data before randomise"
        fslmaths ${CARD4D} -log ${CARD4D}
    fi

    NBVOL=$(fslnvols ${OrigCARD4D})
    #Starting Randomise
    cd ${GLOBAL_DIR}/Results/${NAME}/

    if [[ ! -d "${MET}/${MATRIX}" ]]; then
        mkdir -p ${MET}/${MATRIX}
    fi

    cd ${MET}/${MATRIX}

    #Randomise itself - ========!!! Verify name of 4D files !!!!!========

    echo "Randomising and doing analyses..."
    if [[ ${ANOVA} -eq 0 ]]; then                                               # Bon c'est pas beau ça, on pourrait faire mieux quand même...
        if [[ ${CONFIRMATION} == true && ${CONFIRMATION_MASKS} == true ]]; then # CHANGER SUFFIXE ?

            #RESULT="rdm_${MET}_${NAME}_${MATRIX}_${acquisition}_${MASK}_CM"
            randomise${PARALLEL} -i ${CARD4D_path}/${CARD4D} -o ${RESULT} -d ${GLOBAL_DIR}/Results/ConfirmationMasked/${NAME}_${MATRIX}_${MET}_${acquisition}_${MASK}/${MATRIX}-CM.mat -t ${GLOBAL_DIR}/Results/ConfirmationMasked/${NAME}_${MATRIX}_${MET}_${acquisition}_${MASK}/${MATRIX}-CM.con --vxl=-"${num_waves}" --vxf="${GLOBAL_DIR}/Results/ConfirmationMasked/${NAME}_${MATRIX}_${MET}_${acquisition}_${MASK}/${MATRIX}-CM.nii.gz" -m ${QUALIMASK} -n ${NBPERMUT} -T
            wait
            echo "Analyse for the ${NAME} subjects using the ${MATRIX} matrix, for ${MET}, acquired in ${acquisition} in ${MASK} region has been made in CONFIRMATION MASKED mode" >>../../Result_randomise_${NAME}_${MATRIX}.txt

        else
            #RESULT="rdm_${MET}_${NAME}_${MATRIX}_${acquisition}_${MASK}"
            randomise${PARALLEL} -i ${CARD4D_path}/${CARD4D} -o ${RESULT} -d ../../${MATRIX}.mat -t ../../${MATRIX}.con -m ${QUALIMASK} -n ${NBPERMUT} -T
            wait
            echo "Analyse for the ${NAME} subjects using the ${MATRIX} matrix, for ${MET}, acquired in ${acquisition} in ${MASK} region has been made with ${NBPERMUT} permutations" >>../../Result_randomise_${NAME}_${MATRIX}.txt
        fi
    else
        if [[ ${CONFIRMATION} == true && ${CONFIRMATION_MASKS} == true ]]; then

            #RESULT="rdm_${MET}_${NAME}_${MATRIX}_${acquisition}_${MASK}_CM"
            randomise${PARALLEL} -i ${CARD4D_path}/${CARD4D} -o ${RESULT} -d ${GLOBAL_DIR}/Results/ConfirmationMasked/${NAME}_${MATRIX}_${MET}_${acquisition}_${MASK}/${MATRIX}-CM.mat -t ${GLOBAL_DIR}/Results/ConfirmationMasked/${NAME}_${MATRIX}_${MET}_${acquisition}_${MASK}/${MATRIX}-CM.con -f ../../${MATRIX}.fts --vxl=-"${num_waves}" --vxf="${GLOBAL_DIR}/Results/ConfirmationMasked/${NAME}_${MATRIX}_${MET}_${acquisition}_${MASK}/${MATRIX}-CM.nii.gz" -m ${QUALIMASK} -n ${NBPERMUT} -T -D --fonly
            wait
            echo "Analyse for the ${NAME} subjects using the ${MATRIX} matrix, for ${MET}, acquired in ${acquisition} in ${MASK} region has been made in CONFIRMATION MASKED mode" >>../../Result_randomise_${NAME}_${MATRIX}.txt

        else
            #RESULT="rdm_${MET}_${NAME}_${MATRIX}_${acquisition}_${MASK}"
            randomise${PARALLEL} -i ${CARD4D_path}/${CARD4D} -o ${RESULT} -d ../../${MATRIX}.mat -t ../../${MATRIX}.con -f ../../${MATRIX}.fts -m ${QUALIMASK} -n ${NBPERMUT} -T -D --fonly
            wait
            echo "Analyse for the ${NAME} subjects using the ${MATRIX} matrix, for ${MET}, acquired in ${acquisition} in ${MASK} region has been made with ${NBPERMUT} permutations" >>../../Result_randomise_${NAME}_${MATRIX}.txt
        fi
    fi


    randoanalyze "${MASK}" "${MET}" "${RESULT}" "${CARD4D_path}/${CARD4D}" || return 1

    #done
    # if [[ ${SMOOTHINGCARDS} == false || ${LOGARITHM} == false ]]; then
    # echo "Cleaning"
    # rm ${CARD4D_path}/${CARD4D}
    # fi
}

#------------------- SWE new function -----------------------

sweprocess() {

    # SUFFIX="$1"
    MASK="$1"
    MET="$2"
    RESULT="$3"

    echo "======================================================================================================================================"
    echo "===== SWE ${MET} using ${MATRIX} matrix TFCE ${NBPERMUT} times for ${acquisition} modality in ${MASK}===="
    echo "======================================================================================================================================"

    echo "Initializing..."

    #Checking if we are dealing with a quotient, in this case the QMask will be the same as the SumMetabs to make it more simple.
    METQUOTIENT=false
    for check in ${METABQUOTIENTS[@]}; do
        if [[ "$check" == ${MET} ]]; then
            METQUOTIENT=true
            break
        fi
    done

    met_quali=$(strip_ratio_suffix "${MET}")

    CARD4D_path="${GLOBAL_DIR}/Cartes4D/${NAME}"

    if [[ ${METQUOTIENT} == false && ${type_images} == "mrsi" ]]; then # SI MRSI et pas quotient
        QUALIMASK="${GLOBAL_DIR}/Results/${NAME}/${met_quali}_${NAME}mask${MASK}_quali.nii.gz"
        OrigCARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}" "${NAME}")
        if [[ $? -ne 0 ]]; then
            echo "An error occurred while running name_4d_file. Exiting script."
            exit 1 # Exit script if the function returns an error
        fi
        CARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}-temp" "${NAME}")
        #CARD4D_path="${GLOBAL_DIR}/Cartes4D/${NAME}/"
    elif [[ ${METQUOTIENT} == true && ${type_images} == "mrsi" ]]; then # SI MRSI et quotient -> utilisation du Qmask summetabs (sinon on s'en sort pas)
        QUALIMASK="${GLOBAL_DIR}/Results/${NAME}/${SUM_METAB_LABEL}_${NAME}mask${MASK}_quali.nii.gz"
        OrigCARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}" "${NAME}")
        if [[ $? -ne 0 ]]; then
            echo "An error occurred while running name_4d_file. Exiting script."
            exit 1 # Exit script if the function returns an error
        fi
        CARD4D=$(name_4d_file_mrsi "${data_type}" "${MET}" "${acquisition}-temp" "${NAME}")

    elif [[ ${type_images} != "mrsi" ]]; then
        if [[ ${SMOOTHINGCARDS} == false ]]; then #SI pas de Smoothing

            OrigCARD4D=$(name_4d_file "${data_type}" "${acquisition}" "${MET}" "${NAME}")
            if [[ $? -ne 0 ]]; then
                echo "An error occurred while running name_4d_file. Exiting script."
                exit 1 # Exit script if the function returns an error
            fi
            CARD4D=$(name_4d_file "${data_type}" "${acquisition}-temp" "${MET}" "${NAME}")

        else # SI smoothing (mis automatiquement pour le DTI : valeur du smooth par défaut pour le DTI)
            OrigCARD4D=$(name_4d_file "${data_type}" "${acquisition}" "${MET}-smooth${SMOOTHSIGMA}" "${NAME}")
            if [[ $? -ne 0 ]]; then
                echo "An error occurred while running name_4d_file. Exiting script."
                exit 1 # Exit script if the function returns an error
            fi
            CARD4D=$(name_4d_file "${data_type}" "${acquisition}-temp" "${MET}-smooth${SMOOTHSIGMA}" "${NAME}")
        fi
        if [[ ${type_images} == "dti" ]]; then
            QUALIMASK=$(get_mask_file_path "FA_2500.nii.gz" "dti")
            if [[ -z "$QUALIMASK" ]]; then
                echo "Unable to locate FA_2500.nii.gz for DTI masks. Please prepare masks with Prep_VLAD_b5.sh."
                exit 1
            fi
        else
            local mask_file
            mask_file=$(get_mask_file_path "HarvardOxford-${MASK}.nii.gz" "${type_images}")
            if [[ -z "$mask_file" ]]; then
                echo "Unable to locate HarvardOxford-${MASK}.nii.gz for ${type_images}. Please prepare masks with Prep_VLAD_b5.sh."
                exit 1
            fi
            QUALIMASK="${mask_file}"
        fi

    else
        echo "I don't know where we are but there seems to be an error while loading files for Randomise"
        exit 1
    fi

    #cd $MET
    #Checking for quali QMask
    if [[ ! -e "${QUALIMASK}" && ${type_images} == "mrsi" ]]; then
        wrapmasksmrsi
    fi

    #Checking for 4D card
    if [[ ! -f "${CARD4D_path}/${OrigCARD4D}" ]]; then
        echo "No 4D file found for Randomise, this is a bug. Exiting"
        exit 1
    fi

    #Creating a temp 4D file to make swe lighter
    cd ${CARD4D_path}
    fslmaths ${CARD4D_path}/${OrigCARD4D} -mas ${QUALIMASK} ${CARD4D}

    if [[ ${LOGARITHM} == true ]]; 
    then
        echo "Applying logarithm to the data before randomise"
        fslmaths ${CARD4D} -log ${CARD4D}
    fi

    NBVOL=$(fslnvols ${OrigCARD4D})
    #Starting swe
    cd ${GLOBAL_DIR}/Results/${NAME}/

    if [[ ! -d "${MET}/${MATRIX}" ]]; then
        mkdir -p ${MET}/${MATRIX}
    fi

    cd ${MET}/${MATRIX}

    #swe itself - ========!!! Verify name of 4D files !!!!!========

    echo "Sandwiching and doing analyses..."
    #subfile="../../${MATRIX}_design_sub.txt"
    swe -i ${CARD4D_path}/${CARD4D} -o ${RESULT} -d ../../${MATRIX}.mat -t ../../${MATRIX}.con -s ../../${MATRIX}.sub --wb -n ${NBPERMUT} -T --corrp --modified -m ${QUALIMASK} --glm_output

    echo "SWE analyse for the ${NAME} subjects using the ${MATRIX} matrix, for ${MET}, acquired in ${acquisition} in ${MASK} region has been made" >>../../Result_randomise_${NAME}_${MATRIX}.txt

    #### THIS MUST GO AS A FUNCTION
    # VARIABLES TO PASS : RESULT, SUFFIX, MASK

    randoanalyze "${MASK}" "${MET}" "${RESULT}" "${CARD4D_path}/${CARD4D}" || return 1

    #done
    # if [[ ${SMOOTHINGCARDS} == false || ${LOGARITHM} == false ]]; then
    # echo "Cleaning"
    # rm ${CARD4D_path}/${CARD4D}
    # fi
}


rsumup() {
    # R Processing

    echo "==========================="
    echo "Launching R analysis"
    echo "==========================="
    cd ${GLOBAL_DIR}/Results/${NAME}
    if [[ -e "To_analyse_with_R_${NAME}_${MATRIX}.txt" ]]; then
        if [[ -s "To_analyse_with_R_${NAME}_${MATRIX}.txt" ]]; then

            #MAIN_VARIABLE=$(cat ${GLOBAL_DIR}/Results/${NAME}/main_variable_${MATRIX}.txt) -> no need anymore
            #echo "The main variable selected in Python was: $MAIN_VARIABLE"

            SWE_SUMUP=false
            if [[ ${LONGITUDINAL} == true && ${LONGITUDINAL_TOOL} == "swe" ]]; then
                SWE_SUMUP=true
            fi

            if [[ ${SWE_SUMUP} == true ]]; then
                RMD_TO_USE=${RANALYSE_CREATION_SCRIPT_SWE}
                RMD_BASENAME=$(basename "${RMD_TO_USE}")
                OUTPUT_SUFFIX="${type_images}-swe"
            else
                RMD_TO_USE=${RANALYSE_CREATION_SCRIPT}
                RMD_BASENAME=$(basename "${RMD_TO_USE}")
                OUTPUT_SUFFIX="${type_images}"
            fi

            if [[ ! -f "${RMD_TO_USE}" ]]; then
                echo "R markdown file ${RMD_TO_USE} not found, falling back to ${RANALYSE_CREATION_SCRIPT}"
                RMD_TO_USE=${RANALYSE_CREATION_SCRIPT}
                RMD_BASENAME=$(basename "${RMD_TO_USE}")
                SWE_SUMUP=false
                OUTPUT_SUFFIX="${type_images}"
            fi

            cp "${RMD_TO_USE}" "${GLOBAL_DIR}/Results/${NAME}/"

            #ADD IF EXPLORATORY OR CONFIRMATION ANALYSIS + NB of PERMUTATIONS
            #Pour la V2 on lui passe aussi le path où on enregistre tout ça...
            # => NAME_IMAGE="${MET}_${MATRIX}_${acquisition}_${MASK}" Il faut pouvoir lui envoyer toutes ces informations là. 
            if [[ ${CONFIRMATION} == false ]]; then
                if [[ ${SWE_SUMUP} == true ]]; then
                    Rscript -e "rmarkdown::render('${RMD_BASENAME}', output_file=paste0('$NAME', '_', '$MATRIX', '_', '${OUTPUT_SUFFIX}', '.html'), params=list(analysis_name='$NAME', matrix_name='$MATRIX', number_permut='$NBPERMUT', savepath='${DIR_RANALYSES}', file_type='${type_images}'))" #En faire une variable de ce fucking path
                else
                    Rscript -e "rmarkdown::render('${RMD_BASENAME}', output_file=paste0('$NAME', '_', '$MATRIX', '_', '${OUTPUT_SUFFIX}', '.html'), params=list(analysis_name='$NAME', matrix_name='$MATRIX', number_permut='$NBPERMUT', savepath='${DIR_RANALYSES}'))" #En faire une variable de ce fucking path
                fi
                wait
                cp ${NAME}_${MATRIX}_${OUTPUT_SUFFIX}.html ../${NAME}_${MATRIX}_${OUTPUT_SUFFIX}.html
            else
                if [[ ${SWE_SUMUP} == true ]]; then
                    Rscript -e "rmarkdown::render('${RMD_BASENAME}', output_file=paste0('$NAME', '_', '$MATRIX', '_', '${OUTPUT_SUFFIX}', '-confirmation.html'), params=list(analysis_name='$NAME', matrix_name='$MATRIX', number_permut='$NBPERMUT', savepath='${DIR_RANALYSES}', file_type='${type_images}'))" #Same as plus haut
                else
                    Rscript -e "rmarkdown::render('${RMD_BASENAME}', output_file=paste0('$NAME', '_', '$MATRIX', '_', '${OUTPUT_SUFFIX}', '-confirmation.html'), params=list(analysis_name='$NAME', matrix_name='$MATRIX', number_permut='$NBPERMUT', savepath='${DIR_RANALYSES}'))" #Same as plus haut
                fi
                wait
                cp ${NAME}_${MATRIX}_${OUTPUT_SUFFIX}-confirmation.html ../${NAME}_${MATRIX}_${OUTPUT_SUFFIX}-confirmation.html
            fi

            if (($NBPERMUT < 500)); then
                if [[ ${BATCH_WORKER} == true ]]; then
                    echo "Low-permutation reminder skipped in batch worker mode."
                else
                    echo "Analyse seems to have produce significative results, but with a poor number of permutations. Would you like to run a confirmation later ? y/yes for yes, any other answer will exit"
                    read RUNLATER
                    if [[ $RUNLATER == "y" || $RUNLATER == "yes" || $RUNLATER == "Y" || $RUNLATER == "YES" ]]; then
                        if [[ ! -e "../RandoMRSI_torun.txt" ]]; then
                            touch ../RandoMRSI_torun.txt
                        fi

                        echo "$reconstructed_command --confirmation little" >>../RandoMRSI_torun.txt
                        echo "Command added. Run RandoMRSI_run.sh during a peaceful night ;-)"
                    else
                        echo "Don't forget that your p is +/- 0.04 if permut at 100 and +/- 0.02 if permut at 500 ! See you soon !"
                    fi
                fi
            fi

        else
            echo "Nothing to analyse in R, no significative analysis seems to exist"
        fi
    elif [[ ! -e "To_analyse_with_R_${NAME}_${MATRIX}.txt" ]]; then
        echo "There is a bug, no global file found ! Can't run R without the file... Please check the script"
        exit 1
    else
        echo "No analyse with R asked"
    fi

}

###############################################################
################### RUNNING SCRIPT ############################
###############################################################

if [[ ${PREPARE} == true ]]; then
    echo "Checking names"
    check_names

    echo "Doing Matrix"
    domatrix

    #--------
    # 4D cards + masks part
    #--------

    echo "Cards 4D"
    for cardtodo in ${TODO[@]}; do
        if [[ ${type_images} == "mrsi" ]]; then
            card4dtodo=$(name_4d_file_mrsi "${data_type}" "${cardtodo}" "${acquisition}" "${NAME}")
        else
            card4dtodo=$(name_4d_file "${data_type}" "${acquisition}" "${cardtodo}" "${NAME}")
        fi
        
        #card4dtodo=$(name_4d_file "${data_type}" "${acquisition}" "${cardtodo}" "${NAME}")
        if [[ $? -ne 0 ]]; then
            echo "Error naming 4D file at the checking for 4D card"
            exit 1
        fi
        if [[ ! -e ${GLOBAL_DIR}/Cartes4D/${NAME}/${card4dtodo} ]]; then #We have to find something else that this DO4D variable...
            wrap4dcards ${data_type} ${acquisition} ${cardtodo}

            echo "Checking matrix and 4D card compatibility"
            checkmatrix_4d ${card4dtodo}
        else
            echo "Checking matrix and 4D card compatibility"
            checkmatrix_4d ${card4dtodo}
        fi
    done
else
    if [[ -e "${GLOBAL_DIR}/Listes/List_${NAME}.txt" ]]; then
        SUBJECTS=($(cat ${GLOBAL_DIR}/Listes/List_${NAME}.txt))
    else
        echo "You ask for no preparation of Matrix / subjects file, but we can't find one that fits the analyze. Check for it and remove some options to make the process of matrix doing"
        display_help
        exit 1
    fi

fi

if [[ "${BATCH_MODE}" == "queue" ]]; then
    batch_command=$(build_batch_command "${COHORT}")
    append_command_to_batch "${BATCH_FILE}" "${batch_command}"
    echo "Preparation finished. Analysis command queued. Use --batch run to execute queued commands."
    exit 0
fi

#---------------

# for MASK in ${MASKS[@]}; do

# while true; do
# cd ${GLOBAL_DIR}/Masques
# compare_nifti_files

#--------
# RANDOMISE part
#--------
if [[ ${RANDOMISING} == true ]]; then

    #### Checking masks
    #### Checking masks
    for RANDOMASK in ${MASKS[@]}; do
        mask="HarvardOxford-${RANDOMASK}.nii.gz"
        mask_path=$(get_mask_file_path "${mask}" "${type_images}")
        if [[ -z "$mask_path" ]]; then
            echo "Mask ${mask} not found for modality ${type_images}. Aborting."
            exit 1
        fi
        # echo "looking for reslicing, beta mode"
        # reslice_mask "${mask_path}" "${T1_MNI}"
        # if [[ $? -ne 0 ]]; then
        #     echo "Bug in reslicing masks"
        #     exit 1
        # fi
    done


    # for RANDOMASK in ${MASKS[@]}; do
    #     cd ${GLOBAL_DIR}/Masques/${type_images}
    #     mask="HarvardOxford-${RANDOMASK}.nii.gz"
    #     echo "Checking masks, beta mode"
    #     if [[ ! -e ${mask} ]]; then
    #         cp ../${mask} ${mask}
    #         reslice_mask ${mask}
    #         ### WE STOPPED HERE ### 
    #     fi
        
        
    #     reslice_mask ${mask} 
    #     if [[ $? -ne 0 ]]; then
    #         echo "Bug in reslicing masks"
    #         exit 1
    #     fi
    # done

    if [[ ${type_images} == "mrsi" ]]; then
        wrapmasksmrsi
    fi

    

    cd ${GLOBAL_DIR}/Results/${NAME}
    for analysetodo in ${TODO[@]}; do
        mkdir -p ${analysetodo}
    done

    #Creating sum-up of the analysis
    if [[ ! -e "Result_randomise_${NAME}_${MATRIX}.txt" ]]; then
        touch Result_randomise_${NAME}_${MATRIX}.txt
        echo "Global file with all the results for the Matrix ${MATRIX}" >>Result_randomise_${NAME}_${MATRIX}.txt
        echo "===============================================" >>Result_randomise_${NAME}_${MATRIX}.txt
        date +%F >>Result_randomise_${NAME}_${MATRIX}.txt
        echo "===============================================" >>Result_randomise_${NAME}_${MATRIX}.txt

    else
        echo "===============================================" >>Result_randomise_${NAME}_${MATRIX}.txt
        echo "New Analysis" >>Result_randomise_${NAME}_${MATRIX}.txt
        date +%F >>Result_randomise_${NAME}_${MATRIX}.txt
        echo "===============================================" >>Result_randomise_${NAME}_${MATRIX}.txt
    fi

    if [[ $CONFIRMATION == false && $BATCH_WORKER == false ]]; then
        while true; do
            if [[ ! -e "To_analyse_with_R_${NAME}_${MATRIX}.txt" ]]; then
                echo "We're all good for R"
                touch To_analyse_with_R_${NAME}_${MATRIX}.txt
                break
            else
                echo "/!\ WARNING : Already some analyses done with R or wanted to be sent to R. Type add to add to previous analyses Delete to delete previous. Exit to start again."
                read CONTINUE
                if [[ ${CONTINUE} == "Delete" || ${CONTINUE} == "delete" ]]; then
                    echo "Deleting previous analysis paths for R"
                    rm To_analyse_with_R_${NAME}_${MATRIX}.txt
                    touch To_analyse_with_R_${NAME}_${MATRIX}.txt
                    break
                elif [[ ${CONTINUE} == "add" || ${CONTINUE} == "Add" ]]; then
                    echo "Let's continue then"
                    break
                elif [[ ${CONTINUE} == "exit" || ${CONTINUE} == "Exit" ]]; then
                    echo "Let's start again then :-)"
                    exit 1
                else
                    echo "Options : add / delete / exit"
                fi
            fi
        done
    else
        cd ${GLOBAL_DIR}/Results/${NAME}
        if [[ -e "To_analyse_with_R_${NAME}_${MATRIX}.txt" ]]; then
            rm To_analyse_with_R_${NAME}_${MATRIX}.txt
        fi
        touch To_analyse_with_R_${NAME}_${MATRIX}.txt
    fi

    # Launching Randoextract function

    if [[ -e "${MATRIX}.mat" ]]; then
        if [[ ${CONFIRMATION} == false && ${BATCH_WORKER} == false ]]; then
            total_to_run=${#TODO[@]}
            if ((total_to_run > 20)); then
                read -p "${total_to_run} analyses have to run, which is a lot, are you sure you want to continue? (y/n): " continue_many
                if [[ ${continue_many} != "y" && ${continue_many} != "yes" ]]; then
                    echo "Aborting at user request."
                    exit 1
                fi
            fi
        fi
        for RANDOMASK in ${MASKS[@]}; do

            for thingtodo in ${TODO[@]}; do
                if [[ ${CONFIRMATION} == true && ${CONFIRMATION_MASKS} == true ]]; then
                    confm="_CM"
                else
                    confm=""
                fi

                OUTPUT="rdm_${thingtodo}_${NAME}_${MATRIX}_${acquisition}_${RANDOMASK}${confm}"
                if [[ ${LONGITUDINAL} == true && ${LONGITUDINAL_TOOL} == "swe" ]]; then
                    sweprocess ${RANDOMASK} ${thingtodo} ${OUTPUT}
                else
                    randoextract ${RANDOMASK} ${thingtodo} ${OUTPUT}
                fi
            done
        done
    else
        echo "No matrix found for this name !"
        exit 1
    fi

fi

if [[ ${EXTRACTING} == true ]]; then
    cd ${GLOBAL_DIR}/Results/${NAME}
    if [[ -s "To_analyse_with_R_${NAME}_${MATRIX}.txt" ]]; then
        rm To_analyse_with_R_${NAME}_${MATRIX}.txt
        touch To_analyse_with_R_${NAME}_${MATRIX}.txt
    fi

    for RANDOMASK in ${MASKS[@]}; do

        for thingtodo in ${TODO[@]}; do
            if [[ ${CONFIRMATION} == true && ${CONFIRMATION_MASKS} == true ]]; then
                confm="_CM"
            else
                confm=""
            fi

            OUTPUT="rdm_${thingtodo}_${NAME}_${MATRIX}_${acquisition}_${RANDOMASK}${confm}"
            if ! select_analysis_4d_card "${thingtodo}"; then
                exit 1
            fi

            randoanalyze "${RANDOMASK}" "${thingtodo}" "${OUTPUT}" "${CARD4Dtoanalyze}" || exit 1
        done
    done

fi

if [[ ${SUMUP} == true ]]; then
    rsumup
fi
