# Development and installation

## One-command setup

From a clone or unpacked source archive:

```bash
./install.sh
```

This installs missing system prerequisites, selects or downloads Python,
installs the exact Python dependency lock, sets up FSL with Randomise/SwE,
records runtime paths and runs dependency checks. It reuses a working FSL
installation and keeps cohort configuration and queued commands intact.
It does not require an existing Python, pip, uv, or virtual environment.

On Debian/Ubuntu, missing system packages use apt; sudo may request your
administrator password. On macOS, the installer uses Homebrew and bootstraps
it if absent; the upstream Homebrew setup may request administrator access or
Apple command-line tools. Windows users should run the command in a supported
Linux/WSL environment, or use the [container](../docker/README.md).

Use the application directly without activating the environment:

```bash
./vlad --help
./vlad gui
./vlad doctor --profile full
./vlad prep
```

The launcher also works by absolute path from another directory. After a
standard installation, open a new terminal to use the bare `vlad` command.
The legacy Bash launchers under `scripts/compat/` load the installed runtime
too. They accept the same arguments and locate the checkout independently of
the caller's working directory.

For Python reports and the browser without imaging/desktop dependencies:

```bash
./install.sh --reports-only
./vlad report --help
```

The reports-only installation works with Apple's Bash 3; analysis/preparation
uses Bash 4+ installed by the full setup. Legacy desktop preparation and matrix
dialogs require a graphical display. Browser forms and configured headless
preparation do not. Tk is checked using a Tcl interpreter without opening a
window.

Streamlit is installed by the same command. `./vlad gui` opens the browser
interface at `http://127.0.0.1:2342`; `--headless` starts it without opening a
browser and `--port` chooses another port. Browser design forms do not require
Tk file-selection dialogs. Study paths refer to the machine running the
server. See [browser-gui.md](browser-gui.md) for the workflow and the distinction
between cohort settings and prepared imaging data.

## Installation options and locations

```bash
./install.sh --help
./install.sh --skip-system                 # system prerequisites already present
./install.sh --fsl-dir /path/to/existing/fsl
./install.sh --python /path/to/python3.12
./install.sh --no-add-to-path
```

`--skip-fsl` prevents FSL download and still requires a working installation
for full setup. `--reports-only` skips that requirement. `--no-check` marks the
result unverified; it never prints a verified-ready message. `--yes` requests
noninteractive Homebrew bootstrap. The old `scripts/bootstrap.sh` remains an
entry point to the same installer in reports-only mode.

The installer writes a launcher to `~/.local/bin/vlad` by default and adds the directory
to future Bash, Zsh, Fish or POSIX login shells. Repeated installations reuse
the same launcher. On Windows, `.\scripts\install-windows.ps1`
installs through WSL and also registers a user-level Windows launcher.
Use `-Distribution` to select a non-default WSL distribution, or omit it
when the checkout is already under a WSL network path. To skip PATH setup,
use `--no-add-to-path` on Linux/macOS/WSL or `-NoAddToPath` in PowerShell.

The app uses Python 3.10–3.12, including a Tk check for the full workflow. A
private Python 3.12 is downloaded when no compatible installed interpreter is
available. FSL's own Python is kept separate; its current release uses a newer
Python than the application supports. The installer only adds FSL's
`share/fsl/bin` wrappers to the runtime PATH.

| Location | Installed state |
| --- | --- |
| `.venv/` | Editable application and locked scientific/reporting packages |
| `.tools/uv/bin/` | Pinned uv 0.13.7, downloaded if that version is not available |
| `.local/python/` | Private Python, when needed |
| `.local/fsl/` | New FSL installation, when no existing installation is selected |
| `.local/runtime.env` | Recorded Bash/FSL paths; no study configuration |
| `.cache/` | Downloads, install temporary files and plotting cache |

Existing FSL is detected from an explicit `--fsl-dir`, `FSLDIR`, previously
recorded paths, standard FSL wrappers on PATH, or `~/fsl`. A new destination
must be absent and have no whitespace because of the upstream FSL installer.
An incomplete existing directory is reported and never overwritten. Select a
new `--fsl-dir` or repair that installation before rerunning. FSL is a large
network download; the full installer checks SwE as well as Randomise afterwards.

Python package requirements are declared in `pyproject.toml`; `uv.lock`
records exact versions and hashes. `requirements/python.txt` is a generated
pip export.
For manual development with uv 0.13.7, `uv sync --locked --python PATH --no-dev`
remains available. In an activated compatible virtual environment, pip users
can install `--require-hashes -r requirements/python.txt`, then `--no-deps -e .`.

The downloaded installers come from the official
[uv installer](https://docs.astral.sh/uv/reference/installer/) and
[FSL installer](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/container.html).
The script downloads each installer completely before executing it; a failed
partial download is not run. FSL setup uses a new destination, disables shell
profile changes/registration and selects CPU installation.

## Imaging and Python reporting

The full installer configures FSL automatically. A
[container environment](../docker/README.md) is also available. The workflow needs `fslmaths`,
`fslmerge`, `fslsplit`, `fslstats`, `fslval`, `fslnvols`, `fslmeants`, `flirt`,
`Text2Vest`, `bc`, and `randomise` or `randomise_parallel`. SwE also needs `swe`;
the optional AFNI helper needs `3dLME`. Cluster execution of
`randomise_parallel` depends on the site's scheduler configuration.

All reports are generated in Python. R, RStudio, CRAN packages and Pandoc are
not installed or called by bootstrap, reporting or the container. The old
`--with-r` bootstrap option is rejected with an explanation. Historical R
sources are kept only for auditing and statistical reference comparisons.

| Command | Checks |
| --- | --- |
| `./vlad doctor` | Python and scientific/reporting package imports |
| `./vlad doctor --profile analysis` | Python, Bash, FSL commands, bc and Tk |
| `./vlad doctor --profile prep` | Python, preparation FSL commands, Tk and Zenity |
| `./vlad doctor --profile reports` | Python reporting dependencies, including Arrow |
| `./vlad doctor --profile full` | All required checks; optional backends are noted |

Tk and Zenity need a graphical display for legacy interactive setup. The
browser forms and configured noninteractive preparation do not open desktop
dialogs. Reports and analysis jobs with saved matrices/lists also run without
a desktop display.

Generate a report independently from extracted regional means:

```bash
./vlad report --kind randomise \
  --results-dir /path/to/cohort/Results/PatientsVsControls \
  --analysis-name PatientsVsControls --matrix-name GroupAgeSex \
  --permutations 500 --modality mrsi --export-dir /path/to/reports
```

`--kind swe` selects longitudinal reporting. Use `--subject-column` and
`--visit-column` when legacy column-name guessing is ambiguous. `--output`
sets the HTML destination; `--confirmation` retains confirmation naming and
labels. `--date YYYY-MM-DD` fixes export dates for comparisons.

The main analysis command uses Python reports automatically, including
`--analyze` and longitudinal SwE. Existing HTML basenames and the copy into the
parent results directory are retained. `DIR_RANALYSES` remains the cohort
setting for Feather/SVG exports. The legacy `To_analyse_with_R_*.txt` name is
accepted as a data format; it does not run R. Report errors propagate a nonzero
workflow exit status.

Reports record explicit statistical methods and diagnostics. Tests compare
implemented calculations with known/reference results; a real cohort should
still be checked before treating a changed environment as a study baseline.
See [reporting migration notes](python-reporting-migration.md) for intentional
differences from old source behaviours and the scope of statistical validation.

## Source and local state

| Location | Purpose |
| --- | --- |
| `src/vlad/` | Installable CLI, reporting, checks and Python source |
| `src/vlad/gui/` | Streamlit interface and its cohort, design and job helpers |
| `src/vlad/reporting/` | Report ingestion, statistics, models, plots and HTML |
| `src/vlad/legacy/` | Existing scientific Python helpers |
| `workflows/` | Bash analysis/preparation and shared runtime paths |
| `Masks/` | Six shipped standard masks with stable names/location |
| `config/` | Cohort registry example |
| `scripts/` | Bootstrap and verification commands |
| `scripts/compat/` | Legacy command wrappers, accepting their original arguments |
| `requirements/python.txt` | Generated pip-compatible lock export |
| `tests/` | Launch, reporting and statistical reference checks |
| `docker/` | Dockerfile, container entry point and instructions |
| `docs/` | Workflow guide, development notes and reporting documentation |

The repository root retains `install.sh` and `vlad` as its executable entry
points, alongside project metadata. Compatibility wrappers have moved to
`scripts/compat/`; edit their actual implementations under `src/vlad/` or
`workflows/`. The cohort registry example is
[`config/VLAD_configs.example.txt`](../config/VLAD_configs.example.txt).
Historical R templates are reference material and are excluded from wheel
runtime resources. Cohort variables pointing at old
R templates are no longer used by the report workflow.

The private registry remains `VLAD_configs.txt` in the checkout. It lists
absolute paths to cohort `config_global.sh` files. Blank lines and `#` comments
are accepted. Preparation and analysis read the same registry regardless of
launch directory. Configurations are executable shell scripts; use configs you
control. Keep study inputs and outputs outside source control.

For shared/read-only installations:

```bash
export VLAD_CONFIG_FILE=/path/to/study/VLAD_configs.txt
export VLAD_BATCH_FILE=/path/to/study/VLAD_batch_commands.txt
```

Relative overrides resolve against the launch directory before workflow `cd`
commands. `--batch-file` overrides `VLAD_BATCH_FILE`. Python is selected from
`VLAD_PYTHON_BIN`, `VLAD_PYTHON_ENV`, the checkout's `.venv`, then PATH; an
explicit `--python-env` selects that pyenv environment. The installed CLI uses
its own Python unless overridden. `VLAD_SUBJECT_COLUMN` and `VLAD_VISIT_COLUMN`
pass explicit SwE identifiers through the Bash workflow.

Wheel installs include Python reporting, workflow and mask resources. Set
configuration/batch overrides to writable study locations for a shared wheel.
Development normally uses an editable checkout.

## Checks and dependency updates

```bash
./scripts/check.sh
./vlad report --help
./vlad gui --help
./vlad doctor --profile reports
```

Checks cover launches from unrelated directories, path quoting, cohort
selection, batch failure retention, Python reporting and statistical reference
comparisons. They do not run a full FSL study. Compare a known small cohort's
outputs before using an updated environment for scientific analyses.

To update Python dependencies deliberately:

```bash
uv lock --upgrade
uv sync --locked --no-dev
uv export --locked --no-dev --no-emit-project --output-file requirements/python.txt
./scripts/check.sh
./vlad doctor --profile python
uv build
```

Commit metadata, lock and export together. `uv build` creates a wheel and source
archive in `dist/`. Local environments, caches and outputs are ignored; new
source files are tracked normally. FSL, OS packages and base images are not
locked by the Python lock; preserve a built image digest with study outputs.
The lock/sync behaviour follows the official
[uv project documentation](https://docs.astral.sh/uv/concepts/projects/sync/).
