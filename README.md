# VLAD

VLAD is a local neuroimaging workflow for voxelwise MRI, MRSI and diffusion
analyses. It discovers BIDS-like derivatives, builds population masks and design
matrices, runs FSL Randomise or longitudinal SwE, and produces self-contained
Python HTML reports with statistical maps, cluster summaries and subject-level
plots. R, RStudio and Pandoc are not required.

## 1. Installation

### Full installation

Clone or unpack VLAD, enter its directory, and run one command:

```bash
./install.sh
```

The installer:

- creates a private, locked Python environment;
- installs the browser command generator and Python reporting dependencies;
- checks Bash, Tk, Zenity and the required system libraries;
- reuses an existing FSL installation or installs FSL with Randomise and SwE;
- preserves existing VLAD cohort configurations and working FSL installations.

On Debian/Ubuntu or macOS, missing system packages can be installed through
`apt` or Homebrew. Administrator authentication may be requested. FSL is a
large download, so a first full installation can take time.

Verify the complete environment:

```bash
./vlad doctor --profile full
```

The installer is safe to rerun. To use an existing FSL installation explicitly:

```bash
./install.sh --fsl-dir /path/to/fsl
```

For the browser, command generation and Python reports without FSL or desktop
preparation tools:

```bash
./install.sh --reports-only
./vlad doctor --profile reports
```

### Make `vlad` available from any directory

On Linux, macOS or inside WSL, the standard installation puts `vlad` on
your user PATH:

```bash
./install.sh
```

The installer creates `~/.local/bin/vlad` and configures your shell's PATH.
Open a new terminal and run `vlad --help`. For an isolated install, use
`./install.sh --no-add-to-path`.

On Windows with [WSL and a Linux distribution installed](https://learn.microsoft.com/en-us/windows/wsl/install),
run this from PowerShell in the VLAD checkout. The checkout can be in the
Linux filesystem, accessible from Windows under `\\wsl.localhost\<distribution>\`.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\install-windows.ps1
```

The Windows launcher runs VLAD in WSL. After installation, `vlad` also works
from PowerShell and Command Prompt. Use WSL paths for data arguments, for
example `/mnt/c/data`. Pass `-NoAddToPath` to skip PATH setup. A container setup
is documented in [docker/README.md](docker/README.md).

## 2. Generate and run a VLAD command

### Start the command generator

```bash
./vlad gui
```

VLAD opens the local Streamlit interface at:

```text
http://127.0.0.1:2342
```

All selected paths refer to the computer running VLAD. Browse dialogs choose
local files and directories; they do not upload imaging data. Global settings
can store the main BIDS directory, the Light/Dark theme and the local GUI state
directory.

For a remote server, suppress automatic browser opening:

```bash
./vlad gui --headless
```

Use `--host` and `--port` when a different listen address or port is required.

### Guided MRSI and DWI workflow

The command generator has six steps:

1. **Data** — select **MRSI** or **DWI** and load a covariate TSV containing
   `participant_id` and `session_id`. For MRSI, choose the derivative folder
   and match `space-`, `met-`, optional
   `desc-`, and optional `res-` filename entities. Rows can be filtered or
   explicitly excluded. Visits with missing signal files or required quality
   maps are excluded automatically after path matching. Metabolite entries may
   include voxelwise ratios such as `Glu/GABA` and `Glu/SUMOFMETABOLITES`.
   A `z_` prefix requests gauge correction. For example, `z_Glu GABA` tests
   corrected Glu and raw GABA.
   For DWI, choose a `dwi-preprocessing/` folder, enter the exact shared space
   (such as `mni`), click **Scan DWI folder**, and select one or more 3D scalar
   series. VLAD groups run and acquisition variants by default and counts visits
   with a unique match for each series.
2. **Population mask** — use one precomputed binary mask per metabolite/ratio or
   build population masks from matching quality maps. The defaults are CRLB
   `<20`, FWHM `<0.1` and SNR `>10`, with 68% population coverage.
   For DWI, use common finite nonzero coverage, restrict it to a downloaded
   or custom white-matter mask, or supply a binary mask for each series.
   For MRSI tissue histograms, use the cached TemplateFlow maps for the matching
   space, download them in this step, or supply matching binary GM and WM masks.
3. **Model** — retain the covariate table layout and select `Include`, `Confound`
   or `Regressor` with checkboxes. A covariate cannot be both a confound and a
   regressor: selecting either role clears the other. Exactly one tested
   regressor is required. Tick `Z-score` for any numeric confound or regressor
   to standardize it across the included observations.
4. **Contrasts** — review the exact design-column order. Both suggested
   directions are selected by default; keep either one or both, enter custom
   weights, or load a contrast file. Choose **Omnibus ANOVA** for a categorical
   regressor with three or more included groups to test their joint effect.
5. **Randomise settings** — choose permutations, TFCE, variance smoothing,
   corrected outputs, seed, exchangeability blocks and the number of maps
   processed concurrently.
6. **Command** — choose the study output folder and run name, then click
   **Generate command**. VLAD validates the complete design, creates the study
   `plans/` and `runs/` parent directories, saves an immutable JSON plan, and
   displays the command to execute. It also recaps included covariates, model
   roles, total sample size and categorical/binary regressor group counts.

The Command page does not execute the analysis, monitor jobs or display results.
Copy the generated command and run it in a terminal. It has this form:

```bash
/path/to/VLAD/.venv/bin/python -m vlad workflow --plan /study/plans/MyRun-<hash>.json
```

The workflow creates `/study/runs/MyRun/` only when that command executes. It
refuses to overwrite an existing run folder. Choose an output folder outside
the source derivative trees.

### Generated outputs

A completed guided run contains:

```text
runs/<run-name>/
├── plan.json
├── design.mat
├── design.con
├── design.tsv
├── covariates.tsv
├── selection.tsv
├── subjects.txt
├── results.tsv
├── results.json
├── report.html
├── report_assets/
└── <metabolite-or-ratio>/
    ├── signals.nii.gz
    ├── population_mask.nii.gz
    ├── coverage.nii.gz
    ├── individual_masks/
    └── randomise_*.nii.gz
```

When gauge correction is enabled, raw stacks, the shared `gauge_term.nii.gz`,
per-map group means, z maps and audit metadata are also retained. The HTML report
contains up to eight slices in each anatomical plane, connected-cluster tables
and descriptive subject-level plots for significant corrected maps. With
matching GM and WM maps, a histogram below the slices shows the raw
t- or F-statistic at each voxel that passes the corrected p-value threshold. The
subject-level plot summarizes each included subject/session's mean signal
across those significant voxels.

To prepare matrices, 4D stacks and masks without running Randomise:

```bash
./vlad workflow --plan /study/plans/MyRun-<hash>.json --prepare-only
```

To regenerate the HTML report for an existing completed run without repeating
permutations:

```bash
./vlad workflow --plan /study/runs/MyRun/plan.json --report-only
```

To add tissue histograms to an existing report, pass both
`--gm-mask /path/to/gm.nii.gz` and `--wm-mask /path/to/wm.nii.gz` with
`--report-only`.

The full browser workflow, matching rules, mask construction and report layout
are described in [docs/browser-gui.md](docs/browser-gui.md).

## 3. Other workflows

The **Existing studies** interface retains cohort configuration, TemplateFlow
reference selection, BIDS preparation, DWI/structural workflows, SwE and reports.
Equivalent terminal commands remain available through `vlad configure`,
`vlad prep`, `vlad run` and `vlad report`.

Example Python report regeneration:

```bash
./vlad report --kind randomise \
  --results-dir /study/Results/PatientsVsControls \
  --analysis-name PatientsVsControls \
  --matrix-name GroupAgeSex \
  --permutations 500 \
  --modality mrsi \
  --export-dir /study/reports
```

Historical matrix and snapshot helpers remain available as compatibility
commands. They may open desktop file/Excel dialogs and are not used by the new
guided command generator.

## 4. Repository layout and documentation

```text
src/vlad/          Python CLI, guided workflow, reporting and scientific helpers
workflows/         Bash cohort preparation and established analysis workflow
config/            Example cohort registry and configuration
scripts/           Installation, verification and compatibility wrappers
requirements/      Generated dependency exports
tests/             Unit, integration and statistical reference checks
docker/            Container environment and guide
docs/              Browser, workflow, development and migration documentation
```

Only `install.sh` and `vlad` are primary executable entry points in the project
root. Private configurations, environments, caches and analysis outputs are
ignored. No cohort data is included.

- [Browser command generator](docs/browser-gui.md)
- [Cohort and analysis workflow](docs/workflow.md)
- [Installation and development](docs/development.md)
- [Python reporting migration](docs/python-reporting-migration.md)

Run the repository verification suite with:

```bash
./scripts/check.sh
```

## 5. Complete CLI argument reference

Arguments shown in brackets are optional. Run `./vlad <command> --help` for the
installed command's authoritative help. JSON arguments must be valid JSON in one
shell argument, normally enclosed in single quotes.

### Commands

| Command | Purpose |
|---|---|
| `./vlad gui` | Open the browser command generator and established study interface. |
| `./vlad workflow` | Execute or prepare an immutable guided-workflow plan, or regenerate its report. |
| `./vlad doctor` | Check installed dependencies for a selected capability profile. |
| `./vlad configure` | Save an established-study configuration noninteractively. |
| `./vlad prep` | Prepare established cohort images interactively or from a saved configuration. |
| `./vlad run` | Run the established Bash analysis workflow. |
| `./vlad report` | Generate Randomise or SwE reports in Python. |
| `./vlad matrix-randomise` | Open the legacy Randomise matrix builder. |
| `./vlad matrix-swe` | Open the legacy SwE matrix builder. |
| `./vlad matrix-afni` | Open the legacy AFNI input builder. |
| `./vlad resample-masks` | Resample masks to a reference grid. |
| `./vlad snapshot` | Create a compatibility statistical-map snapshot. |

### Installer arguments

| Argument | Value/default | Description |
|---|---|---|
| `-h`, `--help` | — | Show installer help. |
| `--reports-only` | Off | Install the browser and Python reports without FSL or desktop tools. |
| `--python PATH` | Auto-detected/private Python | Select Python 3.10–3.12; Tk is required for a full installation. |
| `--fsl-dir PATH` | Existing `FSLDIR` or `.local/fsl` | Reuse FSL at this path, or install into a new path. |
| `--skip-system` | Off | Do not install missing system packages. |
| `--skip-fsl` | Off | Do not download FSL; a full installation still validates it. |
| `--no-check` | Off | Skip final dependency checks, leaving the installation unverified. |
| `--add-to-path` | On | Install a user launcher and configure PATH for future shells. |
| `--no-add-to-path` | Off | Skip user launcher and shell PATH setup. |
| `--yes` | Off | Use noninteractive package-manager/bootstrap operation. |

### Global, GUI, doctor and guided-workflow arguments

| Command | Argument | Value/default | Description |
|---|---|---|---|
| `vlad` | `-h`, `--help` | — | Show top-level help. |
| `vlad` | `--version` | — | Print the installed VLAD version. |
| `vlad gui` | `-h`, `--help` | — | Show GUI help. |
| `vlad gui` | `--host HOST` | `127.0.0.1` | Streamlit listen address. |
| `vlad gui` | `--port PORT` | `2342` | Streamlit port, from 1 to 65535. |
| `vlad gui` | `--headless`, `--no-browser` | Off | Start without opening a browser. |
| `vlad doctor` | `-h`, `--help` | — | Show dependency-check help. |
| `vlad doctor` | `--profile PROFILE` | `python` | One of `python`, `analysis`, `prep`, `reports`, or `full`. |
| `vlad workflow` | `-h`, `--help` | — | Show guided-workflow help. |
| `vlad workflow` | `--plan PATH` | Required | Immutable JSON plan generated by VLAD. |
| `vlad workflow` | `--prepare-only` | Off | Create designs, stacks and masks without Randomise. |
| `vlad workflow` | `--report-only` | Off | Regenerate the report for the plan's existing completed run; incompatible with `--prepare-only`. |
| `vlad workflow` | `--gm-mask PATH` | Plan setting | Binary gray matter NIfTI. Supply with `--wm-mask` and `--report-only`. |
| `vlad workflow` | `--wm-mask PATH` | Plan setting | Binary white matter NIfTI. Supply with `--gm-mask` and `--report-only`. |

### `vlad configure` arguments

| Argument | Value/default | Description |
|---|---|---|
| `-h`, `--help` | — | Show configuration help. |
| `--registry PATH` | Required | Cohort registry text file. |
| `--name NAME` | Required | Saved cohort name. |
| `--global-dir PATH` | Required | Study working/output directory. |
| `--reference-image PATH` | Required | Reference anatomical NIfTI. |
| `--subjects-file PATH` | Required | Subject/visit list. |
| `--input-dir PATH` | Required | Imaging derivative input directory. |
| `--exports-dir PATH` | None | Report export directory. |
| `--population-mask PATH` | None | Optional precomputed population mask. |
| `--modality VALUE` | `MRSI` | Study modality. |
| `--space LABEL` | `mni` | Input/reference space label. |
| `--extra-tags TEXT` | Empty | Additional filename matching tags. |
| `--input-mode VALUE` | `bids` | Input matching mode. |
| `--resolution LABEL` | Empty | Optional resolution entity. |
| `--signal-description LABEL` | `signal` | Signal-map `desc-` label. |
| `--qmask-mode VALUE` | `thresholds` | Quality-mask mode. |
| `--crlb-max NUMBER` | `20` | Maximum accepted CRLB value. |
| `--fwhm-max NUMBER` | `0.1` | Maximum accepted FWHM value. |
| `--snr-min NUMBER` | `10` | Minimum accepted SNR value. |
| `--metabolites JSON` | None | JSON list of metabolite labels. |
| `--masks JSON` | None | JSON list of mask paths/names. |
| `--dwi-files JSON` | None | JSON list of DWI derivative patterns/files. |
| `--overwrite` | Off | Allow replacement where supported by cohort saving. |
| `--mask-space-confirmed` | Off | Record that the supplied mask/reference space was confirmed. |
| `--uncompressed` | Off | Store uncompressed NIfTI outputs instead of compressed outputs. |
| `--reference-metadata JSON` | None | JSON object describing the reference source/template. |

### `vlad prep` arguments

| Argument | Value/default | Description |
|---|---|---|
| `-h`, `--help` | — | Show preparation help. |
| `--b0 VALUE` | `3` | MRSI field-strength preset: `3` or `7`. |
| `--overwrite` | Off | Recompute outputs that already exist. |
| `--config PATH` | None | Saved cohort configuration; required for noninteractive BIDS preparation. |
| `--modality VALUE` | None | `MRSI`, `DTI`, or `STRUCTURAL`; required with `--config`. |
| `--subjects PATH` | Whole cohort | Explicit selected subject/visit list; requires noninteractive configured BIDS preparation. |
| `--non-interactive` | Off | Use the supplied configuration without desktop dialogs. |

### `vlad run` arguments

| Argument | Value/default | Description |
|---|---|---|
| `-h`, `--help` | — | Show established-analysis help. |
| `-c`, `--cohort NAME` | Interactive selection | Registered cohort name. |
| `-t`, `--type TYPE` | Required for a new analysis | `MRSI`, `DTI`, or `Structural`. |
| `-n`, `--name NAME` | Required | Analysis/population name. |
| `-m`, `--matrix NAME` | Required in noninteractive mode | Design matrix name. |
| `-a`, `--anova` | Off | Request the legacy ANOVA path. |
| `--b0 VALUE` | `3` | MRSI preset: `3`, `7`, or `7as3`. |
| `--metabs LABEL...` | Configured metabolites | Restrict absolute metabolites; `none` disables absolute maps. |
| `-s`, `--sum [LABEL...]` | Off | Enable sum/ratio-to-sum maps, optionally restricted to labels. |
| `-q`, `--quotients [LABEL...]` | Off | Enable quotient maps, optionally restricted to labels or quoted globs. |
| `-d`, `--description LABEL...` | All validated maps | Restrict Structural/DTI filename descriptions. |
| `-p`, `--perm NUMBER` | `300` | Number of permutations. |
| `--cards [SUFFIX]` | Auto | Force 4D stack creation, optionally with a card suffix. |
| `--mask VALUE` | Configured/default masks | `wmgm`, `wmgmbg`, `onlygrey`, `onlywhite`, `onlygreydetail`, `cerebellum`, or `population`. |
| `--qualithresh PERCENT` | `68` | Population quality coverage; current legacy maximum is 99. |
| `--smooth [SIGMA]` | Off; sigma `1` when omitted | Smooth 4D cards. |
| `--logarithm` | Off | Log-transform data before inference. |
| `-l`, `--longitudinal TOOL` | Off | Longitudinal engine: `swe` or legacy `afni`. |
| `--noparallel` | Off | Use `randomise` instead of `randomise_parallel`. |
| `--confirmation MODE` | Off | `little` or `masked`; uses 10,000 permutations and confirmation behavior. |
| `-cm`, `--confirmation_M` | Off | Build confirmation inputs masked by subject quality masks. |
| `--difflipid` | Off | Use the legacy alternative lipid-suppression map directory. |
| `--remakeall` | Off | Delete and rebuild matching analysis, 4D and list outputs; destructive and unavailable noninteractively. |
| `--prepare` | Off | Run only subject-list, matrix and 4D preparation. |
| `--analyze` | Off | Run only result checking/extraction and Python report generation. |
| `--non-interactive` | Off | Never open dialogs or wait for terminal input; requires cohort, name and matrix. |
| `--batch MODE` | Off | `queue`/`add`, `run`, or internal `worker`. Queue/add is used with `--prepare`. |
| `--batch-file PATH` | Environment/default queue file | Select the batch command file. |
| `--python-env NAME` | Installed interpreter | Use a named pyenv environment for VLAD Python helpers. |

### `vlad report` arguments

| Argument | Value/default | Description |
|---|---|---|
| `-h`, `--help` | — | Show report help. |
| `--kind VALUE` | Required | `randomise` or `swe`. |
| `--results-dir PATH` | Current directory | Directory containing extracted analysis results. |
| `--analysis-name NAME` | Required | Analysis/population name. |
| `--matrix-name NAME` | Required | Matrix/model name. |
| `--permutations NUMBER` | `500` | Permutation count recorded in the report. |
| `--export-dir PATH` | Results-dependent default | Export directory for merged tables and figures. |
| `--modality VALUE` | `mrsi` | Modality label. |
| `--output PATH` | Generated report name | Explicit HTML output path. |
| `--confirmation` | Off | Mark/report a confirmation analysis. |
| `--date YYYY-MM-DD` | Current date | Fixed date for reproducible exports. |
| `--subject-column NAME` | Auto-detected | Explicit subject identifier column. |
| `--visit-column NAME` | Auto-detected | Explicit visit/session column. |
| `--design-columns NAME...` | Exported design order | Exact FSL design/contrast column names. |

### Compatibility helper arguments

| Command | Arguments | Description |
|---|---|---|
| `vlad matrix-randomise` | `ANALYSIS_NAME MATRIX_NAME MAIN_DIRECTORY CREATE_LIST ANOVA` | Legacy Randomise matrix builder; `CREATE_LIST` and `ANOVA` are `0`/`1`. Opens an Excel/sheet dialog. |
| `vlad matrix-swe` | `ANALYSIS_NAME MATRIX_NAME MAIN_DIRECTORY MAKE_LIST` | Legacy SwE matrix builder; `MAKE_LIST` is `0`/`1`. Opens an Excel/sheet dialog. |
| `vlad matrix-afni` | `PREFIX MASK IMG_DIR IMG_SUFFIX` | Legacy AFNI input builder. Opens an Excel/sheet dialog. |
| `vlad snapshot` | `STAT_MAP OUTPUT_NAME COLORMAP ANATOMICAL_IMAGE` | Create a snapshot; `COLORMAP` is `1` for red or `2` for blue. |
| `vlad resample-masks` | `[-h] --masks PATH... --t1 PATH [--backup-dirname NAME] [--replace] [--interp nearest\|continuous]` | Resample 3D/4D masks to the first 3D volume of the reference; default interpolation is `nearest`, default backup directory is `original`. |
