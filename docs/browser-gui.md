# Browser interface

Install VLAD with the usual single command, then open its Streamlit interface:

```bash
./install.sh
./vlad gui
```

The default address is `http://127.0.0.1:2342`. The server opens the browser
unless `--headless` or `--no-browser` is supplied. No virtual-environment
activation is needed. Use `./vlad gui --port 2343` when port 2342 is occupied.
Stop the browser server with Ctrl+C in its launch terminal.

## Guided MRSI workflow

The default interface follows six steps, with **Back** / **Next** controls and a
persistent workflow sidebar:

Use **Global settings** in the sidebar to save the main BIDS directory. VLAD
stores it locally and opens every file/folder Browse dialog at that directory,
including after a server restart. The guided workflow requires the covariate
TSV columns `participant_id` and `session_id`; `subject_id` is not used there.
The same panel selects the persistent Light or Dark theme and the local VLAD
GUI data folder. That folder contains preferences and background-job history.
When it changes, VLAD starts using the new folder and leaves the old one intact.

1. **Data** — load `covars.tsv` containing `participant_id` and `session_id`,
   choose the image folder and `space-` / `met-`
   labels, with optional `desc-` and `res-` selectors. Once the image folder is
   set, **Scan folder** proposes the distinct `met-` labels found recursively in
   NIfTI filenames that also match the current `space-`, `desc-`, and optional
   `res-` selectors; the proposed list remains editable. **Load NIfTI paths** only
   searches filenames; it does not open NIfTI headers or voxel arrays. Each
   subject/session gets a green **✅ Images found** mark only when every selected
   metabolite has exactly one match. Missing or ambiguous matches show **❌** and
   are listed in the matching details. Plain entries such as `Glu` always test
   the raw metabolite map. Ratios are calculated voxelwise from raw maps: use
   `Glu/GABA` for a pair, `Glu/(Glu+GABA+Gln)` for a summed denominator, or
   `(Glu+GABA)/Gln` for a summed numerator (do not put spaces inside an
   expression). `Glu/SUMOFMETABOLITES` remains available
   when the base maps are explicitly listed. A ratio can be entered alone; VLAD
   discovers its source maps. Nonfinite values and zero denominators are
   excluded, and a ratio's subject mask is the intersection of its components'
   quality masks.

   Gauge-corrected maps use a `z` prefix: `z_Glu` selects the gauge-corrected
   Glu map, and `zGlu-zGABA` tests the difference between corrected Glu and GABA
   maps. Include each component map too, as raw entries or `z` entries, for
   example `z_Glu z_GABA zGlu-zGABA`. The prefix activates gauge correction
   automatically. Plain `Glu` and ratios use raw values. Gauge correction
   requires at least two distinct maps, for example `z_Glu GABA`.
   Raw stacks, `gauge_term.nii.gz`,
   per-map group means/z maps and a JSON audit are retained alongside corrected
   testing inputs.

   Use **+ Add filter** to stack categorical
   or numeric covariate rules; active filters combine with AND and can be removed
   individually. Selecting **Exclude** gives the row a red **🔴 Excluded** status.
   **Reset all filters** clears filters while retaining explicit row exclusions.
2. **Population mask** — supply a binary NIfTI for each metabolite, or construct
   masks from configurable quality metrics. Defaults are global `desc-snr` and
   `desc-fwhm`, plus metabolite-specific `desc-crlb`. Global files have no `met-`
   entity; change their scope if your derivatives contain per-metabolite metrics.
   **Load quality paths** adds the second per-visit checkmark. Quality paths must
   match signal space, resolution, run and acquisition. Defaults are CRLB `<20 pc`,
   FWHM `<0.1 Hz` and SNR `>10` (unitless). Units declare the stored file values;
   no Hz/ppm/fraction conversion is performed. For each metabolite and included
   subject, all criteria must pass. The population mask retains a voxel when
   at least the chosen percentage of the **final included subjects** passes.
   Supplied population masks are used directly, after binary/grid validation.
   Under **Tissue masks for report histograms**, download TemplateFlow's GM and
   WM probability maps for the exact analysis space. VLAD reuses them from its
   local `.cache/templateflow` directory or `TEMPLATEFLOW_HOME`. A voxel is
   assigned to the more probable of GM and WM when its probability is at least
   0.5. To override the atlas, choose both non-overlapping binary NIfTIs
   already registered to the analysis space. VLAD samples the masks onto the
   analysis grid with nearest-neighbour interpolation.
3. **Model** — use the Include, Confound and Regressor checkboxes for every
   covariate, with explicit categorical reference levels. Choose one tested
   regressor and any confounds. Checking Confound automatically clears
   Regressor for that variable, and checking Regressor clears Confound, so a
   covariate can never have both roles.
   Tick **Z-score** on a numeric confound or regressor to subtract the mean and
   divide by the population standard deviation of the included observations.
   Standardization is recorded in the plan and leaves the source TSV intact.
   The exact matrix order is intercept, confounds, then regressor columns.
   Missing values and rank-deficient models require explicit correction or
   exclusion; model construction never silently drops rows.
4. **Contrasts** — inspect column positions and use suggested directions, type
   one weight vector per line (for example `0 0 0 1` and `0 0 0 -1`), or load
   a contrast file. Both suggested directions are selected by default; use
   **Contrasts to test** to retain either one or both. Mixed weights compare
   effects; multiple rows run separate directional tests. Suggested categorical labels state the actual comparison
   and reference, such as `Sex: M > F` and `Sex: M < F`; binary numeric coding
   similarly uses `Sex: 1 > 0`. Continuous variables use `slope > 0` and
   `slope < 0`. Changed model coding requires reviewing the contrasts.
5. **Randomise settings** — set permutations (5000 by default; 0 is exhaustive),
   concurrent metabolites (`nproc`, 4 by default), TFCE and its
   H/E/connectivity parameters, voxelwise/uncorrected outputs,
   variance smoothing, seed and optional exchangeability blocks. Repeated
   subjects require an explicitly specified block column or selecting one visit.
   VLAD prepares images sequentially, then runs at most `nproc` metabolite-level
   Randomise processes concurrently. Contrasts inside each metabolite process
   remain sequential. Per-permutation console lines are reduced to compact GUI
   progress bars. In an interactive terminal, Rich displays one live bar for every
   metabolite (including queued metabolites). A bar spans all contrasts: with two
   contrasts, contrast 1 advances it from 0–50% and contrast 2 from 50–100%.
   Raw FSL `randomise` commands bypass this
   wrapper and therefore retain FSL's per-permutation output; the GUI does not
   present them as runnable alternatives.
6. **Command** — the study output folder defaults to the directory containing
   the loaded covariates TSV and remains editable. Choose a new run name, then
   click **Generate command**. Only then does VLAD create the study's `plans/`
   and `runs/` folders, save the immutable reviewed plan, and display the
   copyable command. It also recaps the included covariates and their roles,
   the total included sample, and sample counts for each categorical or binary
   regressor group. Continuous regressors show their total sample count. This
   page does not start or monitor the workflow and does not display logs or
   result tables; execute the generated command in a terminal. The workflow itself creates the self-contained HTML report,
   per-metabolite FSL outputs, 4D stacks, individual masks and population masks.

The headless equivalent is `./vlad workflow --plan /path/to/plan.json`; add
`--prepare-only` to build matrices, stacks and masks without running Randomise.
Use `--report-only` to regenerate the visual report for an existing completed
run without repeating permutations. Add `--gm-mask /path/to/gm.nii.gz` and
`--wm-mask /path/to/wm.nii.gz` together to supply tissue masks for that report.
Each corrected map with significant voxels gets up to eight slices in each
anatomical plane. Beneath the slices, the GM/WM histogram shows the raw
t-statistics for voxels with corrected p ≤ 0.05 in that result. The two
tissues share bins, with heights shown as percentages within each tissue.
The subject-level plot shows one point per included subject/session, averaged
across the same significant voxels.
Each run writes a new `runs/<name>` folder and refuses to replace an existing
one. Its plan records model columns, contrasts, selected-row order, exclusion
audit, quality units/criteria, original paths and input size/mtime fingerprints.
For every metabolite it writes `coverage.nii.gz`, the binary
`population_mask.nii.gz`, `individual_masks/<participant_session>.nii.gz`, and
the `signals.nii.gz` 4D stack under `runs/<name>/<metabolite>/`.
All 4D stacks follow the same explicit model-row order. NIfTI dimensionality,
grid agreement and finite/binary mask values are checked by the background job.

Choose **Existing studies** in the Interface selector to access the established
cohort setup, TemplateFlow anatomy, DWI/structural, SwE and report workflows
outlined below.

## Study setup

Select the cohort registry and a registered cohort in the sidebar. The default
registry is the same `VLAD_configs.txt` used by the command line; set
`VLAD_CONFIG_FILE` before launching to choose another one. You can register an
existing `config_global.sh`, or save cohort settings from the setup form.
The setup form has three tabs: **Study paths**, **Reference anatomy**, and
**Image matching and quality**, with a shared **Save study settings** action.

Choose reference anatomy from the TemplateFlow dropdown, its resolution and,
where applicable, template cohort. Press **Load template** to fetch that image
or reuse a cached copy. Browsing the catalogue does not download anatomy.
The reference template and image **Space label** must agree. A custom `.nii`
or `.nii.gz` path is also supported. TemplateFlow's resolution index identifies
the reference anatomy; the separate image-resolution field matches the exact
`res-` token in your derivative filenames. These need not be the same index or
voxel size. See the [TemplateFlow client documentation](https://www.templateflow.org/usage/client/).

The subject-visit source accepts a `covars.tsv` or CSV with `subject_id` and
`session_id`; `participant_id` is accepted as the BIDS alternative. Identifiers
are read as text, preserving leading zeros, and `sub-`/`ses-` prefixes are
normalised. Duplicate or missing pairs are rejected. VLAD writes the ordered
`SUBJECT_SESSION` list into the study's `Listes/AllSubjects.txt`, preserves the
source table, and preselects it on the design page. The original one-entry-per-line
text format remains supported.

The report-export field follows `<Study folder>/reports` until you enter a
custom path.

Paths belong to the machine running VLAD, including input images, reference
anatomy, subject lists, cohort outputs and report exports. In a container, use
its mounted paths. Enter paths directly or click **Browse…** beside a path field.
The desktop chooser selects a file, folder, or output filename and fills in the
original path. Additional masks support selecting multiple files. Cancelling
leaves the current value unchanged. There are no uploads: choosing inputs does
not transfer or stage files, save settings, or start an analysis.

The chooser opens on the **computer running VLAD**, which needs a desktop
session and Tk (included by the native installer). For a remote/headless server
or a container without desktop access, paste its paths into the editable fields.
The command preview and explicit execution buttons work in either case.

Study setup composes a copyable `vlad configure` command from your inputs.
**Save study settings** executes that command to write configuration and the
normalised subject list. The source table, anatomy and masks remain at their
original paths. Use `./vlad configure --help` for the headless equivalent.

Preparation, analysis and report pages also show their commands before execution.
Changing settings updates the displayed command. Click **Prepare cohort data**,
**Start analysis** or **Generate report** to execute the displayed arguments and
follow the background job's live log. Analysis execution stays disabled until
the required data and design are ready; the command can still be copied.
The command includes the current workspace and cohort registry so copying it
to a terminal uses the same study. Merely viewing a command starts no job.

Use letters, numbers and dashes for cohort, analysis and model names. The
legacy imaging engine currently needs a whitespace-free study folder. Legacy
preparation also needs whitespace-free input/reference/subject paths; the new
Python derivative discovery reads source paths with spaces. Spreadsheet editing
and Python report paths support spaces.

Saving settings establishes the cohort configuration. Use its preparation
action to prepare the selected maps without desktop dialogs. Inputs must still
meet the [cohort preparation requirements](workflow.md); saving a configuration
does not itself preprocess or register images. Existing prepared cohorts can
proceed directly to design and analysis.

### Derivative discovery and masks

**BIDS derivatives** searches the processed-image tree recursively, including
`mrsiprep/` and `dwi-preprocessing/` layouts. It matches each subject/session,
the exact `space-` label, optional `res-` label, and any additional entity
filters such as `run-01`, `acq-dsiNdir129` or `model-SHORE`. Leave resolution
empty only when the selection is unambiguous. Missing images, multiple matches,
raw 4D diffusion series and incompatible image grids stop preparation.

For MRSI, select the `met-` labels and the signal `desc-` label (normally
`signal`). For DWI/DTI, select scalar-map descriptions, suffixes or filename
patterns, such as `dipygfa`, `GFA` or `*desc-dipygfa*_GFA.nii.gz`. The preparation
step copies canonical inputs into `<Study folder>/prepared/`; it never edits
the source derivative tree. Each 4D stack follows the saved design subject
order. A changed preparation manifest or subject list invalidates cached stacks.

The MRSI quality mask uses finite values satisfying all three strict defaults:
CRLB `<20`, FWHM `<0.1` and SNR `>10`. Adjust the thresholds in setup if needed.
Quality maps must match the signal map's space, resolution and grid. The
alternative is a supplied binary population mask containing finite 0/1 values
on that same grid. Preparation exports per-visit masks and per-metabolite
population masks; the run's coverage percentage controls how many selected
visits must pass at each voxel. **Quality population only** applies this mask
without an additional tissue restriction.

Tissue masks must already be registered in the selected image space. The
bundled HarvardOxford masks use legacy MNI coordinates; select them only when
appropriate and confirm their space. Grid resampling does not register anatomy.
Choose a matching tissue preset for DWI/structural analysis; missing masks are
reported before launch. Native subject-specific `space-dwi`/`space-T1w` maps
must be normalised upstream to a shared space for multi-subject voxelwise work.

If the source covariates include visits without usable images, build and filter
the design first. Save it, return to Study setup and choose that saved analysis
under **Population to prepare**. Both matching preview and preparation then use
that explicit subject order; no visits are silently dropped. The CLI equivalent
adds `--subjects /study/Listes/List_MyAnalysis.txt` to the preparation command.
The full source covariate table remains available for other analyses.

For BIDS MRSI inputs, create any desired smoothing, metabolite sums or ratios
upstream and select those derivative labels. The legacy derived-map/smoothing
options are not applied to the new BIDS preparation path. DWI scalar smoothing
can still be selected at analysis time.

The corresponding headless command is also available outside the browser:

```bash
./vlad prep --config /study/config_global.sh --modality MRSI --non-interactive
```

Use `DTI` or `STRUCTURAL` for the other configured modalities. Existing outputs
are retained unless overwrite is explicitly selected. Review the preparation
log before building/running an analysis.

## Design and analysis

The guided design page accepts a local covariate TSV, CSV or Excel
table. It follows the preparation flow used by `mrsi_viewer`'s NBS interface:
select observations, assign covariate roles, inspect the model, then define its
hypotheses. Browser design creation does not open desktop dialogs.

VLAD detects `subject_id` or `participant_id` and `session_id` automatically.
Identifiers retain leading zeros; `sub-` and `ses-` prefixes are removed when
building VLAD's subject/session lists. A BIDS session such as `01` remains `01`.
Older combined `SUBJECT_VISIT` lists remain supported. Randomise accepts one
selected visit per participant; use SwE for repeated observations.

Filter category values or numeric bounds, include or exclude participants or
specific visits, and exclude individual data rows. Review the retained table
and exclusion reasons before selecting a main regressor and nuisance
covariates. Categorical variables can use explicit reference levels. Missing
model values are excluded jointly; a rank-deficient model is rejected. Every
export uses the same retained observation order.

Inspect the generated column names before editing contrast weights. A contrast
has one finite weight per displayed design column and at least one nonzero
weight. You can also import FSL `.con`, plain numeric rows, or CSV/TSV weights.
Named CSV/TSV files contain every design column, in any order, and optionally a
`name`, `label` or `contrast` column; VLAD reorders the weights explicitly to
the design. For example, a model with columns `Intercept`, `Score`, `Age` tests
the adjusted positive Score slope with `0 1 0` and the negative slope with
`0 -1 0`.

Custom weights replace automatic contrasts. For a model with three or more
groups, this also replaces the generated omnibus ANOVA F-test with individual
t-contrasts. The preview explains this change before saving. The saved design
JSON records column coding, reference levels, contrast labels and weights;
`<model>_selection.tsv` records the row-selection audit. Use a new analysis name
when changing a previously saved population so existing 4D data stay aligned.

Randomise reports explicitly distinguish custom voxelwise GLM hypotheses from
the descriptive regional correlations/group comparisons. These selected-region
summaries do not reproduce an adjusted custom GLM test. SwE reports continue
to apply the supplied contrast weights to their regional mixed models.

The run form selects the analysis population, matrix, modality and engine.
Common settings include permutation count, tissue masks, metabolite selection
and quality threshold; advanced options are grouped separately. The interface
uses the same scientific workflow and generated files as `./vlad run`.

Analysis runs use the existing prepared inputs and a saved matrix/subject list.
Resolve readiness messages before starting a job. The browser does not silently
choose values for unanswered scientific or destructive prompts.

## Jobs, logs and reports

The interface lists submitted jobs with their status and captured output. Use
the job controls to refresh logs or stop a running task. Logs and job metadata
are stored locally under `.local/gui/` by default; set `VLAD_GUI_STATE_DIR` to
a writable persistent study directory when sharing an installation or using
Docker.

The reports page discovers HTML reports in the selected cohort. Preview a
report or download it to view independently. Report generation remains Python
only and exports the same Feather/SVG/statistical JSON files as the CLI.

## Server and container use

For a local headless session:

```bash
./vlad gui --headless
```

The default bind is local-only. `--host 0.0.0.0` is available when you explicitly
need network or container access. The interface can access the server's study
files and start analysis processes, so use it on a trusted local machine or
behind your own authenticated access layer rather than exposing it publicly.
The GUI does not supply a multi-user authentication system.

The [container guide](../docker/README.md#browser-interface) shows loopback
port forwarding, study mounts and persistent GUI state. No browser-side imaging
installation is required; FSL and Python execute on the Streamlit server.

Launch configuration follows the official
[Streamlit run documentation](https://docs.streamlit.io/develop/api-reference/cli/run)
and [configuration options](https://docs.streamlit.io/develop/api-reference/configuration/config.toml).
