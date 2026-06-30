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

Make the scripts executable if the clone did not preserve file modes:

```bash
chmod +x VLAD_beta7.sh Prep_VLAD_b6.1.sh
```

Run the preparation script first:

```bash
source .venv/bin/activate
./Prep_VLAD_b6.1.sh
```

The preparation script creates or updates a local `VLAD_configs.txt` file containing paths to cohort `config_global.sh` files. This file is intentionally ignored by git because it contains computer-specific paths. `VLAD_configs.example.txt` shows the expected format.

## Running an analysis

General form:

```bash
./VLAD_beta7.sh -c COHORT -t MRSI -n ANALYSIS_NAME -m MATRIX_NAME -p 500
```

Common options:

- `--prepare`: create lists, matrix files, and 4D cards only.
- `--analyze`: run result checking and R summary only.
- `--batch queue --prepare`: prepare now and queue the analysis command.
- `--batch run`: run queued batch commands.
- `--longitudinal swe`: use the SwE longitudinal workflow.
- `--metabs`, `--sum`, and `--quotients`: choose metabolite analyses.
- `--mask wmgm` or `--mask wmgmbg`: add tissue masks on top of the global quality mask.

For help:

```bash
./VLAD_beta7.sh --help
```

## Files intentionally not committed

The repository ignores local cohort configuration, queued batch commands, analysis outputs, old experiments, publication figures, editor settings, and the separate `VLAD_py/` Python application repository. Those files are either machine-specific, generated, or part of a separate project.
