# VLAD

VLAD is a bash-based workflow for voxel-based analyses of MRSI, DTI, and structural MRI data. It prepares cohort configuration, builds FSL-compatible design matrices from Excel files, runs Randomise or longitudinal SwE analyses, creates result snapshots, and renders HTML summaries from R Markdown.

This repository contains the current bash workflow and the Python/R helper files required by `VLAD_beta7.sh`.

## Included files

- `VLAD_beta7.sh`: main analysis script.
- `Prep_VLAD_b6.1.sh`: interactive cohort/data preparation script.
- `create_matrix_randoMRSI_beta6.py`: design and contrast matrix creation for standard Randomise analyses.
- `create_matrix_VLADlov_swe_alpha2.py`: design, contrast, and subject file creation for FSL SwE longitudinal analyses.
- `create_matrix_VLADlov_afni_alpha1.py`: optional AFNI `3dLME` helper.
- `resample_masks_b3_replace.py`: mask resampling helper used during preparation.
- `snapshot_results_randomise_beta5.py`: PNG snapshot generation for significant maps.
- `Randomise_sum_up_v3.Rmd`: Randomise HTML summary.
- `SWE_sum_up_v1.5.2.Rmd`: SwE HTML summary.

## External prerequisites

Install these outside Python/R before running VLAD:

- Bash 4 or newer.
- FSL with `fslmaths`, `fslmerge`, `fslsplit`, `fslstats`, `fslval`, `flirt`, `Text2Vest`, and `randomise` or `randomise_parallel` in `PATH`.
- FSL SwE command `swe` if using `--longitudinal swe`.
- AFNI with `3dLME` in `PATH` if using `create_matrix_VLADlov_afni_alpha1.py`.
- R, Rscript, and Pandoc for the HTML reports.
- A Python 3 installation with Tk support, because the matrix helpers open an Excel file-selection dialog.
- `zenity` for the interactive preparation script dialogs on Linux. On macOS, install it with Homebrew if needed.

## Python setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

When running the main script, either keep this environment activated or point VLAD to it:

```bash
export VLAD_PYTHON_BIN="$(pwd)/.venv/bin/python"
```

If you use `pyenv`, the main script also accepts:

```bash
./VLAD_beta7.sh --python-env venv-mri ...
```

`Prep_VLAD_b6.1.sh` currently calls `python3` directly for mask resampling, so activate the environment before running preparation.

## R setup

From the repository root:

```bash
Rscript install_R_packages.R
```

The R package list is stored in `requirements.R`. Some packages, especially `arrow`, can require system libraries depending on the operating system.

## First-time VLAD setup

Run all setup commands from the repository root. The setup script stores paths relative to where it is installed, so avoid launching it from another working directory.

Make the scripts executable if the clone did not preserve file modes:

```bash
chmod +x VLAD_beta7.sh Prep_VLAD_b6.1.sh
```

Activate the Python environment before preparation, because `Prep_VLAD_b6.1.sh` calls `python3` directly for mask resampling:

```bash
source .venv/bin/activate
./Prep_VLAD_b6.1.sh
```

For 7T MRSI preparation, pass the B0 mode during preparation:

```bash
./Prep_VLAD_b6.1.sh --b0 7
```

The setup script is `Prep_VLAD_b6.1.sh`. If you see older notes mentioning `VLAD_b6.1.sh` or `setup_VLAD.sh`, they refer to this preparation step.

## Cohort Setup

A cohort is one configured study/data root. Each cohort has its own `GLOBAL_DIR`, subject list, modality configuration, masks, results, and generated files.

When `Prep_VLAD_b6.1.sh` starts, it reads `VLAD_configs.txt` from the VLAD repository. If the file already contains cohort configs, it lists them by `COHORT` name and lets you choose one or create another. If it does not exist, it creates one.

For a new cohort, the script asks for:

- `GLOBAL_DIR`: the main directory for this cohort. VLAD creates `Cartes4D/`, `Masques/`, `Results/`, `Scripts/`, and `Listes/` inside it.
- `COHORT`: the short cohort name used later with `./VLAD_beta7.sh -c COHORT`.
- `T1_MNI`: anatomical reference image used for snapshots and mask alignment.
- `DIR_RANALYSES`: where rendered R summary reports should be copied.
- `LIST_ALL_SUBJECTS`: a text file containing all available subjects and visits.

The subject list must contain one subject/visit per line, using the format:

```text
SUBJECT_VISIT
```

For example:

```text
001_V1
001_V2
002_V1
```

VLAD expects a single underscore separator: the text before the underscore becomes `sub-`, and the text after it becomes `ses-`. The examples above are expected to match files such as `sub-001_ses-V1_...`.

After global setup, choose the modality to configure. For MRSI, type:

```text
mrsi
```

The MRSI setup asks for:

- `NII_DIR_MRSI`: directory containing the MRSI NIfTI files. VLAD searches recursively inside this directory.
- Compression: whether files end in `.nii.gz` or `.nii`.
- `MRSI_SPACE_LABEL`: the value after `space-`, default `mni`.
- `MRSI_EXTRA_TAGS`: optional BIDS-like tags inserted after the space label, for example `res-3.0mm`.
- `MRSI_MASK_LIST`: masks to prepare for MRSI. These are copied/resampled under the cohort `Masques/` tree.
- Metabolite names. Defaults are `CrPCr NAA NAAG GPCPCh Ins Glu Gln GABA GSH`.
- Per-metabolite file descriptions. Defaults are `signal_filtbiharmonic_pvcorr` and `crlb`.
- Global MRSI file descriptions. Defaults are `fwhm` and `snr`.

Expected MRSI filenames are:

```text
sub-SUBJECT_ses-VISIT_space-SPACE_met-METAB_desc-DESCRIPTION_mrsi.nii.gz
sub-SUBJECT_ses-VISIT_space-SPACE_desc-snr_mrsi.nii.gz
sub-SUBJECT_ses-VISIT_space-SPACE_desc-fwhm_mrsi.nii.gz
```

Example:

```text
sub-001_ses-V1_space-mni_met-NAANAAG_desc-signal_filtbiharmonic_pvcorr_mrsi.nii.gz
sub-001_ses-V1_space-mni_met-NAANAAG_desc-crlb_mrsi.nii.gz
sub-001_ses-V1_space-mni_desc-snr_mrsi.nii.gz
sub-001_ses-V1_space-mni_desc-fwhm_mrsi.nii.gz
```

During MRSI preparation, VLAD validates the expected files, creates derived sum/ratio/quotient maps, and creates subject-level quality masks using SNR, FWHM, and CRLB. Missing files are reported in `GLOBAL_DIR/not_found.txt`.

The preparation script creates:

- `GLOBAL_DIR/config_global.sh`
- `GLOBAL_DIR/config_mrsi.sh`
- an entry in local `VLAD_configs.txt` pointing to `GLOBAL_DIR/config_global.sh`

`VLAD_configs.txt` is intentionally ignored by git because it contains computer-specific absolute paths. `VLAD_configs.example.txt` shows the expected format.

## Cohort Selection

The main script selects a cohort from `VLAD_configs.txt`.

Use `-c` when you know the cohort name:

```bash
./VLAD_beta7.sh -c ARMS -t MRSI -n AllSubjects -m PatientsVsControls
```

If `-c` is omitted, VLAD lists all configured cohorts and asks you to select one interactively. The `-c` value must match the `COHORT` value stored in the selected cohort's `config_global.sh`.

## Analysis Name and Matrix Name

`-n` and `-m` are not interchangeable.

`-n` or `--name` is the analysis/population name. It identifies the selected population and controls these generated paths:

```text
GLOBAL_DIR/Listes/List_<NAME>.txt
GLOBAL_DIR/Cartes4D/<NAME>/
GLOBAL_DIR/Results/<NAME>/
```

Use a stable name for the group of subjects included in the analysis, for example:

```bash
-n AllSubjects
-n PatientsVsControls
-n BaselineOnly
```

If `List_<NAME>.txt` does not exist, VLAD can create it while building the matrix. The matrix helper opens an Excel file, asks for the sheet and columns to include, drops rows with missing values, and writes the selected subject list. It defaults to an Excel subject column named `Nom_dossier`; if that column is absent, it asks for the subject column name. The subject values must use the same `SUBJECT_VISIT` format as the global subject list.

`-m` or `--matrix` is the design/contrast name. It controls files under `GLOBAL_DIR/Results/<NAME>/`, such as:

```text
<MATRIX>.mat
<MATRIX>.con
<MATRIX>.csv
main_variable_<MATRIX>.txt
```

Use a name that describes the statistical model, for example:

```bash
-m Group
-m GroupAgeSex
-m SymptomsAgeSex
```

Avoid spaces and underscores in both names. VLAD warns about underscores because they can break parts of the R summary. Prefer letters, numbers, and dashes.

## Running an Analysis

General form:

```bash
./VLAD_beta7.sh -c COHORT -t MRSI -n ANALYSIS_NAME -m MATRIX_NAME -p 500
```

Example MRSI group comparison using absolute metabolites, ratios to sum, quotients, and detailed tissue masks:

```bash
./VLAD_beta7.sh \
  -c ARMS \
  -t MRSI \
  -n PatientsVsControls \
  -m GroupAgeSex \
  -p 500 \
  --metabs NAANAAG CrPCr Ins GPCPCh GluGln \
  --sum \
  -q '*onCrPCr' \
  --mask wmgmbg
```

Preparation only:

```bash
./VLAD_beta7.sh -c ARMS -t MRSI -n PatientsVsControls -m GroupAgeSex --sum --prepare
```

Analyze existing Randomise/SwE results and render the R summary only:

```bash
./VLAD_beta7.sh -c ARMS -t MRSI -n PatientsVsControls -m GroupAgeSex --analyze
```

Prepare now and queue the analysis for later:

```bash
./VLAD_beta7.sh -c ARMS -t MRSI -n PatientsVsControls -m GroupAgeSex --sum --prepare --batch queue
./VLAD_beta7.sh --batch run
```

## Important MRSI Options

- `-t MRSI`: selects the MRSI branch. The value is case-insensitive.
- `--b0 3`: default 3T-style analysis using `SumMetabs` and `RatioSum`.
- `--b0 7`: 7T-style analysis using 9-metabolite sums where available, with `SumMetabs9` and `RatioSum9`.
- `--b0 7as3`: analyze 7T data with the 3T-style 5-metabolite set, using `SumMetabs5` and `RatioSum5`.
- `--metabs MET1 MET2`: analyze only selected absolute metabolites.
- `--metabs none`: skip absolute metabolite analyses.
- `-s` or `--sum`: include sum and ratio-to-sum analyses. Without values, VLAD uses all configured sum outputs. With values, it restricts to selected sum/ratio outputs.
- `-q` or `--quotients`: include metabolite quotient analyses. You can list exact quotients or shell globs. Quote globs in zsh, for example `-q '*onCrPCr'`.
- `--mask wmgm`: analyze `white-grey-matter`, `white-matter`, and `grey-matter`.
- `--mask wmgmbg`: analyze `white-grey-matter`, `white-matter`, `cortex`, and `subcortical-nuclei`.
- `--mask onlygrey`, `--mask onlywhite`, `--mask onlygreydetail`, or `--mask cerebellum`: narrower mask presets.
- `--qualithresh 50`: require at least 50 percent of subjects to have valid quality mask coverage in a voxel. Default is `68`.
- `--cards`: force recreation of 4D cards. Optionally pass a card suffix after it.
- `--smooth [sigma]`: create smoothed 4D cards. Default sigma is `1` if no value is given.
- `--logarithm`: apply log transform before Randomise or SwE.
- `-p 500`: number of permutations. Default is `300`.
- `--noparallel`: use `randomise` instead of `randomise_parallel`.
- `--longitudinal swe`: use the FSL SwE workflow and the SwE R summary.
- `--confirmation little`: rerun with higher permutations and warnings disabled.
- `--confirmation masked`: confirmation mode using subject-level quality masks.
- `-cm` or `--confirmation_M`: prepare masked confirmation design files.
- `--remakeall`: delete the current `Cartes4D/<NAME>`, `Results/<NAME>`, and `Listes/List_<NAME>.txt` outputs before rebuilding.

Because `-m` means matrix, use the long option `--mask` for tissue masks.

For help:

```bash
./VLAD_beta7.sh --help
```

## Files intentionally not committed

The repository ignores local cohort configuration, queued batch commands, analysis outputs, old experiments, publication figures, editor settings, and the separate `VLAD_py/` Python application repository. Those files are either machine-specific, generated, or part of a separate project.
