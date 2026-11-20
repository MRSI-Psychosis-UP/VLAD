# Container environment

The Ubuntu 22.04 image packages the Python workflow and reporting, Tk, Zenity,
and optionally FSL. It contains no R or Pandoc runtime. Python dependencies come
from the same `uv.lock` as local development, using uv 0.13.7 and Ubuntu's Python
3.10 with Tk. The image uses Python wheels instead of compiling the former R
package stack.

Ubuntu packages, the base image and the FSL installer are not version-locked;
preserve a built image by digest alongside study outputs for reproduction.

Build from the repository root, using the Dockerfile under `docker/` as
the build definition and the repository root as the build context:

```bash
docker build -f docker/Dockerfile -t vlad:dev .
```

FSL is installed by default and makes this a large, network-intensive build.
For Python development and report generation without FSL:

```bash
docker build -f docker/Dockerfile --build-arg INSTALL_FSL=false -t vlad:dev-no-fsl .
```

The image follows the [FSL container installation instructions](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/container.html)
and [uv's locked Docker workflow](https://docs.astral.sh/uv/guides/integration/docker/).
AFNI is an external optional dependency and is not installed. Check whether your
FSL installation supplies `swe` before using the longitudinal workflow.

## Run and retain configuration

```bash
docker run --rm -it vlad:dev
docker run --rm vlad:dev doctor --profile full
```

The reporting environment can be checked without FSL using:

```bash
docker run --rm vlad:dev-no-fsl ./scripts/check.sh
docker run --rm vlad:dev-no-fsl doctor --profile reports
docker run --rm vlad:dev-no-fsl report --help
```

The tests use synthetic data and exercise Python rendering, exports and
statistical calculations. They do not run an FSL cohort analysis.

Keep data, cohort configurations, reports, and batch queues in the mounted
study directory. All paths written into cohort configurations must refer to
paths visible **inside the container** (for example `/work/study` rather than
`/home/user/study`). Choose `/work/study/reports` for `DIR_RANALYSES`.

For an already prepared cohort, place a `VLAD_configs.txt` containing the
container-visible paths to its `config_global.sh` files in the host study
folder, then run:

```bash
docker run --rm -it \
  -v /path/to/study:/work/study \
  -e VLAD_CONFIG_FILE=/work/study/VLAD_configs.txt \
  -e VLAD_BATCH_FILE=/work/study/VLAD_batch_commands.txt \
  vlad:dev vlad run -c COHORT -t MRSI -n ANALYSIS -m MATRIX -p 500
```

The previous `vlad:dev vlad -c COHORT ...` image command remains supported.
The recommended CLI now uses the `run` subcommand. A matrix and subject list
must already exist for unattended analysis; otherwise the helper opens a Tk
file dialog and prompts in the terminal. FSL's `randomise_parallel` may require
additional scheduler configuration; `--noparallel` selects plain `randomise`.

The image runs as root by default, so new files in a bind mount may be owned by
root on Linux. Where file ownership matters, use
`--user "$(id -u):$(id -g)" -e HOME=/tmp` with a writable mounted study folder.
Scripts must be able to read the installed source and write to the mounted
study and export directories.

## Browser interface

Expose the browser interface only on the local host and keep study paths
inside the mounted folder:

```bash
docker run --rm -it \
  -p 127.0.0.1:2342:2342 \
  -v /path/to/study:/work/study \
  -e VLAD_CONFIG_FILE=/work/study/VLAD_configs.txt \
  -e VLAD_GUI_STATE_DIR=/work/study/.vlad-gui \
  vlad:dev gui --host 0.0.0.0 --headless
```

Open `http://127.0.0.1:2342` in the host browser. The explicit container bind
address allows Docker port forwarding; the host publication remains loopback.
Use container-visible paths such as `/work/study` in cohort forms. The mounted
GUI state directory retains job records/logs when the container is removed.
`vlad:dev-no-fsl` supports report browsing and Python-only work; imaging runs
need the FSL-enabled image. Browser design forms do not need X11 forwarding.
The desktop preparation command still has its documented display requirements.

See the [browser guide](../docs/browser-gui.md) for the interface workflow.

## Interactive preparation on Linux/X11

Both Tk dialogs and Zenity require a working graphical display. For a Linux
host with X11/Xwayland and a readable Xauthority file, the following passes the
host's existing display credentials without changing display access controls:

```bash
docker run --rm -it \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v /path/to/study:/work/study \
  -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
  -v "${XAUTHORITY:?Set XAUTHORITY to your readable X11 authority file}:/tmp/vlad.xauth:ro" \
  -e DISPLAY="$DISPLAY" -e XAUTHORITY=/tmp/vlad.xauth \
  -e VLAD_CONFIG_FILE=/work/study/VLAD_configs.txt \
  -e VLAD_BATCH_FILE=/work/study/VLAD_batch_commands.txt \
  vlad:dev prep
```

This configuration is Linux-specific. Docker Desktop on macOS/Windows and
Wayland-only sessions need their own display forwarding setup; the image does
not provide a desktop server. Xvfb is included for rendering but does not let
you interact with file dialogs. Existing matrix/list/config files allow
unattended analysis without GUI forwarding.

## Updating dependencies

Regenerate and validate `uv.lock` using the repository development instructions
before rebuilding. `--locked` intentionally fails if metadata and lock disagree.
Use `docker build -f docker/Dockerfile --pull --no-cache ...` when intentionally
refreshing the unlocked OS/FSL layers, then revalidate study outputs.

Building requires Docker daemon access, image registries, PyPI, Ubuntu package
repositories and, when selected, the FSL download servers. It does not contact
CRAN or install R packages. Local source checks do not substitute for building
the image and validating it against a study dataset.
