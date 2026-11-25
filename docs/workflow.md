# Cohort and analysis guide

Installation and environment checks are described in [development.md](development.md).
Examples below use the checkout launcher `./vlad`. From another directory, use
its absolute path or the installed `vlad` command. Legacy command wrappers are
available under `scripts/compat/` and accept their original arguments.

## Cohort Setup

A cohort is one configured study/data root. Each cohort has its own `GLOBAL_DIR`, subject list, modality configuration, masks, results, and generated files.

When `./vlad prep` starts, it reads `VLAD_configs.txt` from the VLAD repository. If the file already contains cohort configs, it lists them by `COHORT` name and lets you choose one or create another. If it does not exist, it creates one.

For a new cohort, the script asks for:

- `GLOBAL_DIR`: the main directory for this cohort. VLAD creates `Cartes4D/`, `Masques/`, `Results/`, `Scripts/`, and `Listes/` inside it.
- `COHORT`: the short cohort name used later with `./vlad run -c COHORT`.
- `T1_MNI`: anatomical reference image used for snapshots and mask alignment.
- `DIR_RANALYSES`: where Python reports export Feather tables and SVG plots. HTML stays in the cohort results tree.
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

## Standard Masks

The repository includes standard masks in `Masks/`. During cohort configuration, when `./vlad prep` asks for `MRSI_MASK_LIST`, you can select one or more of these files. The preparation script copies them into the cohort mask directory, or resamples them to the cohort reference image when needed:

```text
GLOBAL_DIR/Masques/<COHORT>/mrsi/
```

Keep the filenames unchanged, because the analysis workflow searches for masks by these names when using `--mask`.

If you want every `--mask` preset to be available later, select all six standard masks during MRSI cohort configuration.

Included masks:

- `HarvardOxford-white-grey-matter.nii.gz`: default whole white plus grey matter mask.
- `HarvardOxford-white-matter.nii.gz`: white matter mask.
- `HarvardOxford-grey-matter.nii.gz`: grey matter mask.
- `HarvardOxford-cortex.nii.gz`: cortical grey matter mask.
- `HarvardOxford-subcortical-nuclei.nii.gz`: subcortical nuclei mask.
- `HarvardOxford-white-grey-matter-cerebellum.nii.gz`: white plus grey matter plus cerebellum mask.

Mask presets in `./vlad run` use these files:

- default: `HarvardOxford-white-grey-matter.nii.gz`
- `--mask wmgm`: white-grey-matter, white-matter, grey-matter
- `--mask wmgmbg`: white-grey-matter, white-matter, cortex, subcortical-nuclei
- `--mask onlygrey`: grey-matter
- `--mask onlywhite`: white-matter
- `--mask onlygreydetail`: grey-matter, cortex, subcortical-nuclei
- `--mask cerebellum`: white-grey-matter-cerebellum

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
Legacy preparation writes the derived MRSI maps and quality masks under
`GLOBAL_DIR/Results/prepared-mrsi/`. The original derivative folder remains
an input.

The preparation script creates:

- `GLOBAL_DIR/config_global.sh`
- `GLOBAL_DIR/config_mrsi.sh`
- an entry in local `VLAD_configs.txt` pointing to `GLOBAL_DIR/config_global.sh`

`VLAD_configs.txt` is intentionally ignored by git because it contains computer-specific absolute paths. [`config/VLAD_configs.example.txt`](../config/VLAD_configs.example.txt) shows the expected format.

## Cohort Selection

The main script selects a cohort from `VLAD_configs.txt`.

Use `-c` when you know the cohort name:

```bash
./vlad run -c ARMS -t MRSI -n AllSubjects -m PatientsVsControls
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

Avoid spaces and underscores in both names. The legacy workflow may warn about underscores in names. Prefer letters, numbers, and dashes.

## Running an Analysis

General form:

```bash
./vlad run -c COHORT -t MRSI -n ANALYSIS_NAME -m MATRIX_NAME -p 500
```

Example MRSI group comparison using absolute metabolites, ratios to sum, quotients, and detailed tissue masks:

```bash
./vlad run \
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
./vlad run -c ARMS -t MRSI -n PatientsVsControls -m GroupAgeSex --sum --prepare
```

Analyze existing Randomise/SwE results and generate the Python summary only:

```bash
./vlad run -c ARMS -t MRSI -n PatientsVsControls -m GroupAgeSex --analyze
```

Prepare now and queue the analysis for later:

```bash
./vlad run -c ARMS -t MRSI -n PatientsVsControls -m GroupAgeSex --sum --prepare --batch queue
./vlad run --batch run
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
- `--longitudinal swe`: use the FSL SwE workflow and the Python SwE summary.
- `--confirmation little`: rerun with higher permutations and warnings disabled.
- `--confirmation masked`: confirmation mode using subject-level quality masks.
- `-cm` or `--confirmation_M`: prepare masked confirmation design files.
- `--remakeall`: delete the current `Cartes4D/<NAME>`, `Results/<NAME>`, and `Listes/List_<NAME>.txt` outputs before rebuilding.

Because `-m` means matrix, use the long option `--mask` for tissue masks.

For help:

```bash
./vlad run --help
```

## Files intentionally not committed

The repository ignores local cohort configuration, queued batch commands, analysis outputs, editor settings, and the separate `VLAD_py/` Python application repository. Keep other study data and publication artifacts outside the checkout; new development source is tracked normally.
