"""Local browser interface for VLAD's cohort, design, execution and reports."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from vlad.paths import project_root
from vlad.gui.cohorts import (
    list_cohorts, list_reports, register_cohort, registry_path,
)
from vlad.gui.commands import (
    AnalysisOptions, analysis_command, preparation_command, report_command,
    runtime_environment, validate_analysis, settings_command, command_text,
)
from vlad.gui.design import (
    DesignOptions, detect_identifier_columns, filter_rows, load_contrasts,
    load_table, preview_design, sheet_names, write_design,
)
from vlad.gui.jobs import JobStore, parse_progress
from vlad.gui.file_picker import path_input, path_list_input
from vlad.gui.theme import apply_theme, hero


PAGES = ("Overview", "Study setup", "Design", "Run analysis", "Reports")
ACTIVE = {"queued", "starting", "running", "stopping"}
MODALITIES = ("MRSI", "DTI", "STRUCTURAL")
MASKS = {
    "Quality population only · no tissue restriction": "population",
    "Whole white and grey matter": "default",
    "White matter and grey matter separately": "wmgm",
    "Detailed tissue masks": "wmgmbg",
    "Grey matter only": "onlygrey",
    "White matter only": "onlywhite",
    "Detailed grey matter": "onlygreydetail",
    "Whole brain including cerebellum": "cerebellum",
}
MODES = {
    "Prepare and run analysis": "run",
    "Prepare analysis files only": "prepare",
    "Summarise existing analysis results": "analyze",
}
ENGINES = {"Randomise · cross-sectional": "randomise", "SwE · repeated measures": "swe"}
DEFAULT_METABOLITES = "CrPCr NAA NAAG GPCPCh Ins Glu Gln GABA GSH"


@st.cache_resource
def _job_store(directory: str) -> JobStore:
    return JobStore(Path(directory))


def _save_global_gui_settings(state_dir: Path) -> None:
    """Button callback runs before path widgets are instantiated on the rerun."""
    from vlad.gui.settings import save_gui_settings

    try:
        settings = save_gui_settings(
            state_dir,
            bids_dir=str(st.session_state.get("global_bids_dir", "")),
            theme=str(st.session_state.get("global_theme", "Light")),
            settings_dir=str(st.session_state.get("global_settings_dir", "")),
        )
        st.session_state["global_bids_dir"] = settings["bids_dir"]
        st.session_state["global_theme"] = settings["theme"]
        st.session_state["global_settings_dir"] = settings["settings_dir"]
        if not st.session_state.get("wf_images"):
            st.session_state["wf_images"] = settings["bids_dir"]
        st.session_state["global_settings_notice"] = "Global GUI settings saved."
        st.session_state.pop("global_settings_error", None)
    except (OSError, ValueError) as exc:
        st.session_state["global_settings_error"] = str(exc)
        st.session_state.pop("global_settings_notice", None)


def _tokens(value: str) -> list[str]:
    return [part for part in re.split(r"[,\s]+", value.strip()) if part]


def _path(value: str) -> Path:
    if not value.strip():
        raise ValueError("Enter a file or folder path.")
    return Path(value.strip()).expanduser().resolve()


def _go(page: str) -> None:
    st.session_state["pending_page"] = page
    st.rerun()


def _job_metadata(cohort, action: str, analysis: str = "", matrix: str = "") -> dict:
    return {"cohort": cohort.name, "workspace": str(cohort.global_dir), "action": action,
            "analysis_name": analysis, "matrix_name": matrix}


def _active_jobs(store: JobStore, cohort=None) -> list[dict]:
    values = store.list()
    return [job for job in values if job.get("status") in ACTIVE and
            (cohort is None or job.get("metadata", {}).get("workspace") == str(cohort.global_dir))]


def _workspace_busy(store: JobStore, directory: Path) -> bool:
    return any(job.get("status") in ACTIVE and job.get("metadata", {}).get("workspace") == str(directory)
               for job in store.list())


def _start_job(store: JobStore, root: Path, registry: Path, command: list[str], label: str,
               metadata: dict | None = None) -> dict | None:
    try:
        job = store.start(command, cwd=root, env=runtime_environment(root, registry),
                          label=label, metadata=metadata or {})
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(str(exc))
        return None
    st.session_state["selected_job"] = job["id"]
    st.session_state["activity_job_selector"] = job["id"]
    st.success(f"Started: {label}. Follow its output below or return later.")
    return job


def _cohort_required(cohort) -> bool:
    if cohort is not None:
        return True
    st.info("Choose a cohort in the sidebar, or add one in Study setup.")
    if st.button("Set up a study", key="setup_empty_page", type="primary"):
        _go("Study setup")
    return False


def _subject_count(cohort) -> int:
    value = cohort.values.get("LIST_ALL_SUBJECTS", "")
    if not value or not isinstance(value, (str, Path)):
        return 0
    try:
        return sum(1 for line in Path(value).read_text().splitlines() if line.strip() and not line.lstrip().startswith("#"))
    except (OSError, UnicodeError):
        return 0


def _overview(root: Path, registry: Path, cohorts: list, cohort, store: JobStore) -> None:
    jobs = store.list()
    reports = list_reports(cohort) if cohort else []
    columns = st.columns(4)
    columns[0].metric("Registered cohorts", len(cohorts))
    columns[1].metric("Subject visits", _subject_count(cohort) if cohort else "—")
    columns[2].metric("Active jobs", len([job for job in jobs if job.get("status") in ACTIVE]))
    columns[3].metric("Reports", len(reports))
    st.write("")
    steps = st.columns(3)
    with steps[0]:
        st.subheader("1 · Set up the study")
        st.write("Connect cohort folders and prepare processed images, masks, and quality maps.")
        if st.button("Open study setup", width="stretch"):
            _go("Study setup")
    with steps[1]:
        st.subheader("2 · Build the design")
        st.write("Enter the path to an Excel or text table and choose the subjects and model variables.")
        if st.button("Build a design", disabled=cohort is None, width="stretch"):
            _go("Design")
    with steps[2]:
        st.subheader("3 · Run and review")
        st.write("Launch the workflow, watch its progress, and open the resulting Python report.")
        if st.button("Configure an analysis", disabled=cohort is None, width="stretch"):
            _go("Run analysis")
    st.divider()
    left, right = st.columns([2, 1])
    with left:
        if cohort:
            st.subheader(cohort.name)
            st.caption("Study folder")
            st.code(str(cohort.global_dir), language=None)
            st.write("Configured modalities: " + (", ".join(cohort.modalities) or "None yet"))
            for warning in cohort.warnings:
                st.warning(str(warning))
        else:
            st.info("No cohort is selected. Register an existing configuration or create a new study to begin.")
    with right:
        st.subheader("Check the tools")
        st.write("Check Python, FSL, SwE, and the setup tools before starting a study.")
        if st.button("Check tools", disabled=bool(_active_jobs(store)), width="stretch"):
            _start_job(store, root, registry,
                       [sys.executable, "-m", "vlad", "doctor", "--profile", "full"],
                       "Check installed analysis tools", {"action": "diagnostics"})
    _jobs_panel(store, cohort)


def _setup(root: Path, registry: Path, cohort, store: JobStore, state_dir: Path) -> None:
    mode = st.radio("How would you like to add a study?", ["Create study settings", "Register an existing cohort"], horizontal=True)
    if mode == "Register an existing cohort":
        config_file = path_input("Existing cohort configuration", key="setup_existing_config", extensions=(".sh",),
                                 placeholder="/data/study/config_global.sh",
                                 help="Choose a configuration already created by VLAD on this machine.")
        submitted = st.button("Register cohort", type="primary")
        if submitted:
            try:
                saved = register_cohort(registry, _path(config_file))
                st.session_state["pending_cohort"] = str(saved.config_file)
                st.session_state["setup_notice"] = f"Registered {saved.name}."
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(str(exc))
    else:
        st.caption("Enter paths on the machine running VLAD. The command below updates as you fill in the settings; files stay in their original locations. Nothing runs until you click its execution button.")
        paths_tab, anatomy_tab, matching_tab = st.tabs(
            ["Study paths", "Reference anatomy", "Image matching and quality"])
        with paths_tab:
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Cohort name", placeholder="MyStudy", help="A short study name, using letters, numbers and dashes.")
                study_dir = path_input("Study folder", placeholder="/data/MyStudy", key="setup_study_dir", kind="directory", allow_new=True,
                                          help="VLAD keeps prepared copies, designs and results here. Source images are read only.")
                input_dir = path_input("Processed image folder", key="setup_input_dir", kind="directory", placeholder="/data/derivatives/mrsiprep",
                                          help="A derivatives tree such as mrsiprep or dwi-preprocessing; searched recursively in BIDS mode.")
                modality = st.selectbox("Image modality", MODALITIES, format_func=lambda value: "DWI / DTI" if value == "DTI" else value)
            with col2:
                subjects_path = path_input("All subject visits · covariates or text file", key="setup_subjects_path", extensions=(".txt", ".tsv", ".csv"), placeholder="/data/derivatives/covars.tsv",
                                              help="TSV/CSV with subject_id (or participant_id) and session_id; alternatively one SUBJECT_VISIT per text line.")
                default_export = str(Path(study_dir).expanduser() / "reports") if study_dir.strip() else ""
                previous_default = st.session_state.get("setup_export_default", "")
                if st.session_state.get("setup_exports", "") in ("", previous_default):
                    st.session_state["setup_exports"] = default_export
                st.session_state["setup_export_default"] = default_export
                exports_dir = path_input("Report export folder", key="setup_exports", kind="directory", allow_new=True, help="Defaults to Study folder/reports. Enter another folder to keep a custom location.")
        with anatomy_tab:
            reference_metadata = None
            reference_source = st.radio("Reference source", ["TemplateFlow database", "Custom NIfTI file"], horizontal=True)
            reference = ""
            reference_space_hint = st.session_state.get("setup_image_space", "MNI152NLin2009cAsym")
            if reference_source == "Custom NIfTI file":
                reference = path_input("Reference anatomical image", key="setup_reference", extensions=(".nii", ".nii.gz"), placeholder="/data/templates/T1_MNI.nii.gz")
            else:
                try:
                    from vlad.gui.templates import list_templates, template_options, fetch_template
                    templates = list_templates()
                    default_template = templates.index("MNI152NLin2009cAsym") if "MNI152NLin2009cAsym" in templates else 0
                    template = st.selectbox("TemplateFlow template", templates, index=default_template)
                    reference_space_hint = template
                    options = template_options(template)
                    st.caption(options.get("name", template))
                    left, middle, right = st.columns(3)
                    resolution_options = [str(value) for value in options.get("resolutions", [])]
                    def resolution_label(value):
                        zooms = options.get("metadata", {}).get("res", {}).get(value, {}).get("zooms", [])
                        return value + (" · " + " × ".join(f"{zoom:g}" for zoom in zooms) + " mm" if zooms else "")
                    resolution = left.selectbox("Template resolution", resolution_options or ["Default"], format_func=resolution_label)
                    cohorts = [str(value) for value in options.get("cohorts", [])]
                    template_cohort = middle.selectbox("Template cohort", cohorts or ["Default"])
                    suffixes = [str(value) for value in options.get("suffixes", ["T1w"])]
                    suffix = right.selectbox("Anatomical contrast", suffixes, index=suffixes.index("T1w") if "T1w" in suffixes else 0)
                    selection = (template, resolution, template_cohort, suffix)
                    if st.button("Load template", help="Fetch this reference from TemplateFlow, or reuse its local cached copy."):
                        with st.spinner("Loading reference anatomy…"):
                            resolved = fetch_template(template, resolution=None if resolution == "Default" else resolution,
                                                      cohort=None if template_cohort == "Default" else template_cohort, suffix=suffix)
                        st.session_state["loaded_template"] = (selection, str(resolved))
                    loaded = st.session_state.get("loaded_template")
                    if loaded and loaded[0] == selection:
                        reference = loaded[1]
                        reference_metadata = {"template": template, "resolution": resolution, "cohort": template_cohort, "suffix": suffix}
                        st.success("Reference ready: " + reference)
                    else:
                        st.caption("Select an anatomy and press Load template before saving. Downloads happen only when requested.")
                except (ImportError, ValueError, OSError, RuntimeError) as exc:
                    st.error(f"TemplateFlow reference unavailable: {exc}")
        with matching_tab:
            input_mode_label = st.radio("Input organisation", ["BIDS derivatives · recursive search", "Legacy VLAD filenames"], horizontal=True)
            input_mode = "bids" if input_mode_label.startswith("BIDS") else "legacy"
            first, second = st.columns(2)
            with first:
                if st.session_state.get("setup_image_space", "") in ("", st.session_state.get("setup_space_default", "")):
                    st.session_state["setup_image_space"] = reference_space_hint
                st.session_state["setup_space_default"] = reference_space_hint
                space = st.text_input("Space label", key="setup_image_space", help="Exact value following space- in filenames. Initially follows the selected TemplateFlow template; all maps and reference anatomy must use this space.")
                resolution = st.text_input("Image resolution label (optional)", placeholder="3", help="Exact res- value in filenames; for res-3 use 3. Leave empty only when there is one unambiguous resolution.")
                extra_tags = st.text_input("Additional filename tags", placeholder="run-01_model-SHORE", help="Optional exact filename entities such as run-, acq- or model- to resolve multiple matches; applies to BIDS and legacy inputs.")
                st.caption("Use a common anatomical/template space across subjects. Native-space DWI volumes need registration to a common space before voxelwise analysis.")
                metabolites = st.text_input("MRSI metabolites", value=DEFAULT_METABOLITES, disabled=modality != "MRSI")
                signal_description = st.text_input("MRSI signal descriptor", value="signal", disabled=modality != "MRSI",
                                                    help="Exact desc- label identifying metabolite concentration/signal maps.")
                dwi_files = st.text_area("DWI scalar map selectors", value="gFA", disabled=modality != "DTI",
                                         help="One desc label, suffix, or filename glob per line. Select 3D scalar maps, not raw 4D diffusion series.", height=85)
            with second:
                quality_label = st.radio("MRSI quality mask", ["Create from CRLB, FWHM and SNR", "Use a precomputed population mask"], disabled=modality != "MRSI")
                qmask_mode = "thresholds" if quality_label.startswith("Create") else "population"
                crlb_max = st.number_input("CRLB must be below", min_value=0.0, value=20.0, disabled=modality != "MRSI" or qmask_mode != "thresholds")
                fwhm_max = st.number_input("FWHM must be below", min_value=0.0, value=0.1, step=0.01, format="%.3f", disabled=modality != "MRSI" or qmask_mode != "thresholds")
                snr_min = st.number_input("SNR must be above", min_value=0.0, value=10.0, disabled=modality != "MRSI" or qmask_mode != "thresholds")
                population_mask = path_input("Population binary mask", key="setup_population_mask", extensions=(".nii", ".nii.gz"), placeholder="/data/masks/population.nii.gz", disabled=modality != "MRSI" or qmask_mode != "population")
            with st.expander("Tissue masks and filename details"):
                masks = sorted((root / "Masks").glob("*.nii.gz"))
                selected_masks = st.multiselect("Standard masks", [str(path) for path in masks], default=[] if input_mode == "bids" else [str(path) for path in masks],
                                                format_func=lambda value: Path(value).name.removesuffix(".nii.gz"))
                extra_masks = path_list_input("Additional mask paths", key="setup_extra_masks", extensions=(".nii", ".nii.gz"), placeholder="One mask path per line", height=90)
                st.caption("Bundled tissue masks use legacy MNI coordinates. Resampling changes their voxel grid; it does not register a mask to another anatomical space.")
                masks_confirmed = st.checkbox("I confirm the selected tissue masks use the image space", value=False,
                                              help="Confirm these tissue masks are already registered in the image Space label. Resampling alone does not register anatomy.")
                compressed = st.checkbox("Images use .nii.gz compression", value=True, disabled=input_mode == "bids",
                                           help="Legacy filenames only. BIDS discovery accepts .nii and .nii.gz.")
        overwrite = st.checkbox("Replace existing cohort settings", value=False,
                                help="Use only when updating configuration files in an already configured study folder.")
        mask_paths = [*selected_masks, *[line.strip() for line in extra_masks.splitlines() if line.strip()]]
        command = None
        required = {"Cohort name": name, "Study folder": study_dir, "Processed image folder": input_dir,
                    "Reference anatomical image": reference, "All subject visits": subjects_path}
        missing = [label for label, value in required.items() if not value.strip()]
        st.subheader("Study settings command")
        if missing:
            st.info("Complete these fields to compose the command: " + ", ".join(missing) + ".")
        else:
            folder = _path(study_dir)
            settings = dict(
                registry=registry, name=name.strip(), global_dir=folder, reference_image=_path(reference),
                subjects_file=_path(subjects_path), exports_dir=_path(exports_dir) if exports_dir.strip() else folder / "reports",
                modality=modality, input_dir=str(_path(input_dir)), compression=compressed,
                space=space.strip(), extra_tags=extra_tags.strip(), metabolites=_tokens(metabolites), masks=mask_paths,
                overwrite=overwrite, input_mode=input_mode, resolution=resolution.strip(),
                signal_description=signal_description.strip(), dwi_files=[line.strip() for line in dwi_files.splitlines() if line.strip()],
                qmask_mode=qmask_mode, crlb_max=float(crlb_max), fwhm_max=float(fwhm_max), snr_min=float(snr_min),
                population_mask=_path(population_mask) if qmask_mode == "population" and population_mask.strip() else None,
                reference_metadata=reference_metadata, mask_space_confirmed=masks_confirmed,
            )
            command = settings_command(settings)
            st.code(command_text(command, root, registry), language="bash")
            st.caption("Save study settings runs this command. It creates configuration and subject-list files; image preparation is a separate command below.")
        submitted = st.button("Save study settings", type="primary", disabled=command is None)
        if submitted and command:
            try:
                if _workspace_busy(store, folder):
                    raise ValueError("A job is using this study folder. Wait for it to finish or stop it before changing its settings.")
                if mask_paths and not masks_confirmed:
                    raise ValueError("Confirm that the selected tissue masks use the image space, or clear the tissue-mask selection.")
                result = subprocess.run(command, cwd=root, env=runtime_environment(root, registry),
                                        capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise ValueError(result.stderr.strip() or result.stdout.strip() or "Could not save study settings.")
                st.session_state["pending_cohort"] = str(folder / "config_global.sh")
                st.session_state["setup_notice"] = f"Saved {name.strip()}. You can now prepare its data below."
                st.rerun()
            except (ValueError, OSError, RuntimeError, UnicodeError, subprocess.TimeoutExpired) as exc:
                st.error(str(exc))
    notice = st.session_state.pop("setup_notice", None)
    if notice:
        st.success(notice)
    if cohort:
        st.divider()
        st.subheader(f"Prepare {cohort.name}")
        st.write("Validate the configured image files, create derived maps and quality masks, and align the selected masks.")
        selected_population = None
        if any(cohort.values.get(f"{value}_INPUT_MODE") == "bids" for value in cohort.modalities):
            lists = sorted((cohort.global_dir / "Listes").glob("List_*.txt"))
            selected_population = st.selectbox(
                "Population to prepare", [None, *lists],
                format_func=lambda value: "All visits from the study source" if value is None else value.stem.removeprefix("List_"),
                help="Create/filter a design first, then select its saved population to prepare only those visits. The source covariates remain intact.",
                key=f"prep_population:{cohort.config_file}")
            st.caption("If input matching reports missing visits, exclude them explicitly in Design, save the design, and select its population here.")
        if any(cohort.values.get(f"{value}_INPUT_MODE") == "bids" for value in cohort.modalities):
            if st.button("Preview input matching"):
                try:
                    from vlad.preparation import preview_inputs
                    for value in cohort.modalities:
                        if cohort.values.get(f"{value}_INPUT_MODE") != "bids":
                            continue
                        with st.spinner(f"Finding {value} maps…"):
                            preview = preview_inputs(cohort.config_file, value, subjects_file=selected_population)
                        st.subheader(f"{value} input matches")
                        st.caption(f"{len(preview.get('subjects', []))} subject visits · {preview.get('image_count', 0)} matched images")
                        for category in ("missing", "ambiguous", "errors"):
                            for problem in preview.get(category, [])[:30]:
                                st.warning(str(problem))
                        if preview.get("rows"):
                            st.dataframe(pd.DataFrame(preview["rows"]), width="stretch", hide_index=True)
                except (ImportError, ValueError, OSError, RuntimeError) as exc:
                    st.error(f"Could not inspect inputs: {exc}")
        columns = st.columns(3)
        modalities = cohort.modalities or list(MODALITIES)
        modality = columns[0].selectbox("Modality to prepare", modalities)
        b0 = columns[1].selectbox("MRSI field strength", ["3", "7"], format_func=lambda value: value + " T")
        overwrite = columns[2].checkbox("Recompute existing prepared files", value=False)
        try:
            command = preparation_command(root, cohort, modality=modality, b0=b0, overwrite=overwrite,
                                          subjects_file=selected_population if cohort.values.get(f"{modality}_INPUT_MODE") == "bids" else None)
            st.subheader("Preparation command")
            st.code(command_text(command, root, registry), language="bash")
            if st.button("Prepare cohort data", type="primary", disabled=bool(_active_jobs(store, cohort))):
                _start_job(store, root, registry, command, f"Prepare {cohort.name} · {modality}",
                           _job_metadata(cohort, "prepare"))
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
        _jobs_panel(store, cohort)


def _read_design_source(default_path: str = ""):
    value = path_input("Study table path", value=default_path, placeholder="/data/derivatives/covars.tsv",
                       key="design_table_path", extensions=(".xlsx", ".csv", ".tsv"))
    if value.strip():
        path = _path(value)
        if not path.is_file():
            st.info("Enter the path to an existing spreadsheet or text table.")
            return None, None
        if path.stat().st_size > 20 * 1024 * 1024:
            st.error("Choose a spreadsheet smaller than 20 MB.")
            return None, None
        return path, path.name
    return None, None


def _design(root: Path, cohort, store: JobStore) -> None:
    if not _cohort_required(cohort):
        return
    st.subheader("1 · Load the covariate table")
    data, filename = _read_design_source(str(cohort.values.get("COVARIATES_FILE", "")))
    if data is None:
        st.info("Choose a covars.tsv, CSV or Excel table to build the study design.")
        return
    try:
        sheets = sheet_names(data, filename=filename)
        sheet = st.selectbox("Worksheet", sheets, key="design_sheet") if sheets else None
        frame = load_table(data, filename=filename, sheet_name=sheet)
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        st.error(f"Could not read this table: {exc}")
        return
    columns = list(frame.columns)
    st.caption(f"{len(frame):,} rows · {len(columns)} columns · {filename}")
    detected_subject, detected_visit = detect_identifier_columns(frame)
    subject_default = detected_subject or next((name for name in ("subject_visit", "Nom_dossier", "subject") if name in columns), columns[0])
    first, second = st.columns(2)
    subject_column = first.selectbox("Subject or subject-visit column", columns, index=columns.index(subject_default))
    visit_options = ["Combined in subject column", *[name for name in columns if name != subject_column]]
    visit_default = detected_visit or next((name for name in ("visit", "session", "time", "visit_id") if name in columns), None)
    visit_choice = second.selectbox("Session / visit column", visit_options, index=visit_options.index(visit_default) if visit_default in visit_options else 0)
    visit_column = None if visit_choice == "Combined in subject column" else visit_choice
    st.caption("subject_id or participant_id and session_id are detected automatically. BIDS sub-/ses- prefixes are normalised consistently with image matching.")
    st.subheader("2 · Select the analysis population")
    filters = []
    filter_columns = st.multiselect("Filter covariate columns", [name for name in columns if name not in (subject_column, visit_column)],
                                    help="Add inclusion rules. All rules must pass; the audit records every excluded row.")
    for column in filter_columns:
        key = "filter:" + hashlib.sha256(column.encode()).hexdigest()[:12]
        left, right = st.columns([1, 2])
        operator = left.selectbox(f"Rule for {column}", ["Include values", "Exclude values", "At least", "At most", "Between", "Has a value"], key=key + ":rule")
        values = []
        if operator in ("Include values", "Exclude values"):
            choices = sorted(frame[column].dropna().astype(str).unique().tolist())
            values = right.multiselect(f"Values for {column}", choices, default=choices if operator == "Include values" else [], key=key + ":values")
        elif operator != "Has a value":
            text = right.text_input(f"Numeric limit for {column}", placeholder="For Between, enter lower, upper", key=key + ":number")
            if text.strip():
                try:
                    values = [float(value) for value in _tokens(text)]
                except ValueError:
                    st.error(f"Enter numeric limits for {column}.")
                    return
            else:
                st.info(f"Enter the numeric limit for {column}.")
                return
        filters.append({"column": column, "operator": {"Include values": "in", "Exclude values": "not in", "At least": ">=", "At most": "<=", "Between": "between", "Has a value": "not missing"}[operator], "values": values})
    first, second = st.columns(2)
    included_subjects = first.text_area("Include only these subjects / visits (optional)", help="Space/comma separated subject IDs or SUBJECT_VISIT values. Empty includes everyone passing the rules.", height=85)
    excluded_subjects = second.text_area("Exclude these subjects / visits", help="Subject-only IDs remove all visits; SUBJECT_VISIT values remove one visit.", height=85)
    excluded_rows = st.multiselect("Exclude individual table rows", list(range(1, len(frame) + 1)), help="One-based data-row numbers shown in the inclusion audit.")
    try:
        selection = filter_rows(frame, subject_column, visit_column=visit_column, filters=filters,
                                include_subjects=_tokens(included_subjects) or None,
                                exclude_subjects=_tokens(excluded_subjects) or None, excluded_rows=excluded_rows)
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(str(exc))
        return
    selected_frame = selection["frame"]
    first, second = st.columns(2)
    first.metric("Selected observations", len(selected_frame))
    second.metric("Excluded observations", len(frame) - len(selected_frame))
    st.dataframe(selected_frame.head(100), width="stretch", hide_index=True)
    with st.expander("Review the inclusion audit"):
        st.dataframe(selection["audit"], width="stretch", hide_index=True)
        st.download_button("Download inclusion audit", selection["audit"].to_csv(index=False).encode(), file_name="inclusion-audit.csv", mime="text/csv")
    if selected_frame.empty:
        st.warning("No observations pass the selected inclusion rules.")
        return
    st.subheader("3 · Define the regressor and nuisance variables")
    engine_label = st.radio("Design family", list(ENGINES), horizontal=True, key="design_family")
    engine = ENGINES[engine_label]
    measured = [name for name in columns if name != subject_column and (name != visit_column or engine == "swe")]
    if not measured:
        st.info("The table needs at least one measured regressor in addition to identifiers.")
        return
    main_default = next((name for name in ("group", "Group", "Diag", "Score", "score", "Age", "age") if name in measured), measured[0])
    first, second = st.columns(2)
    with first:
        analysis = st.text_input("Analysis name", value="AllSubjects", key="design_analysis_name", help="A population name using letters, numbers and dashes.")
        main_variable = st.selectbox("Main variable", measured, index=measured.index(main_default), help="The regressor or group effect to test.")
    with second:
        matrix = st.text_input("Model name", value="MainEffect", key="design_matrix_name")
        predictors = st.multiselect("Adjust for these covariates", [name for name in measured if name != main_variable], help="Nuisance variables are adjusted for; they are not tested unless a contrast explicitly includes them.")
    model_columns = [main_variable, *predictors]
    automatic_factors = [name for name in model_columns if pd.to_numeric(selected_frame[name], errors="coerce").notna().sum() != selected_frame[name].notna().sum() or selected_frame[name].nunique() == 2]
    categorical = st.multiselect("Treat as categorical factors", model_columns, default=automatic_factors,
                                  help="Numeric codes can be marked categorical. Binary variables are represented as factors automatically.")
    reference_levels = {}
    for name in categorical:
        levels = sorted(selected_frame[name].dropna().astype(str).unique().tolist())
        if levels:
            reference_levels[name] = st.selectbox(f"Reference level · {name}", levels)
    with st.expander("Design details"):
        factor_label = "Factor contrasts (3+ levels)" if engine == "swe" else "ANOVA"
        kind_names = {"Detect automatically": "auto", "Continuous association": "continuous", "Two-group comparison": "group", factor_label: "anova"}
        kind_label = st.selectbox("Analysis type", list(kind_names))
        group_column = st.selectbox("Group column for longitudinal summaries", ["None", *columns]) if engine == "swe" else "None"
        demean = st.checkbox("Centre nuisance covariates", value=True)
        overwrite = st.checkbox("Replace existing design files", value=False)
    options = DesignOptions(analysis_name=analysis.strip(), matrix_name=matrix.strip(), subject_column=subject_column,
                            main_variable=main_variable, predictors=predictors, kind=engine, visit_column=visit_column,
                            group_column=None if group_column == "None" else group_column,
                            analysis_type=kind_names[kind_label], demean_covariates=demean,
                            categorical_columns=categorical, reference_levels=reference_levels,
                            selection_audit=selection["audit"].to_dict("records"))
    try:
        preview = preview_design(selected_frame, options)
    except (ValueError, OSError, RuntimeError) as exc:
        st.warning(f"Adjust the design: {exc}")
        return
    st.subheader("4 · Define the contrasts")
    st.caption("Weights correspond exactly to the design columns below. Positive and negative weights define the direction of each test; nuisance weights are normally zero.")
    st.dataframe(preview["design"].head(100), width="stretch")
    contrast_mode = st.radio("Contrast definition", ["Suggested contrasts", "Edit contrast weights", "Load a contrast file"], horizontal=True)
    contrast_error = None
    try:
        if contrast_mode == "Edit contrast weights":
            contrast_frame = pd.DataFrame(preview["contrasts"], columns=preview["design"].columns)
            contrast_frame.insert(0, "name", preview["contrast_labels"])
            editor_key = "contrast-editor:" + hashlib.sha256(json.dumps([engine, list(preview["design"].columns), preview["contrast_labels"]]).encode()).hexdigest()[:16]
            edited = st.data_editor(contrast_frame, num_rows="dynamic", hide_index=True, width="stretch", key=editor_key)
            options.contrasts = edited.drop(columns="name").apply(pd.to_numeric, errors="raise").to_numpy().tolist()
            options.contrast_labels = edited["name"].fillna("").astype(str).tolist()
        elif contrast_mode == "Load a contrast file":
            contrast_path = path_input("Contrast file path", key="design_contrast_path", extensions=(".con", ".tsv", ".csv", ".txt"), placeholder="/data/study/contrasts.tsv")
            if not contrast_path.strip():
                raise ValueError("Enter a contrast file path to continue.")
            loaded = load_contrasts(_path(contrast_path), list(preview["design"].columns))
            options.contrasts = loaded["contrasts"].tolist()
            options.contrast_labels = loaded["labels"]
        preview = preview_design(selected_frame, options)
        contrasts = pd.DataFrame(preview["contrasts"], columns=preview["design"].columns, index=preview["contrast_labels"])
        st.dataframe(contrasts, width="stretch")
        st.download_button("Download contrast table", contrasts.rename_axis("name").to_csv(sep="\t").encode(), file_name="contrasts.tsv", mime="text/tab-separated-values")
        for warning in preview.get("warnings", []):
            st.warning(str(warning))
    except (ValueError, OSError, RuntimeError) as exc:
        contrast_error = str(exc)
        st.error(contrast_error)
    preview_column, save_column = st.columns(2)
    preview_clicked = preview_column.button("Preview design", width="stretch", disabled=bool(contrast_error))
    save_clicked = save_column.button("Save design", type="primary", width="stretch", disabled=bool(contrast_error) or bool(_active_jobs(store, cohort)))
    signature = hashlib.sha256((selected_frame.to_csv(index=False) + repr(options)).encode()).hexdigest()
    if preview_clicked or save_clicked:
        try:
            if save_clicked and _active_jobs(store, cohort):
                raise ValueError("A job is using this study. Wait for it to finish before replacing its design.")
            result = write_design(selected_frame, options, cohort.global_dir, overwrite=overwrite) if save_clicked else preview
            st.session_state["design_result"] = result
            st.session_state["design_result_labels"] = (cohort.name, analysis, matrix)
            st.session_state["design_result_signature"] = signature
            if save_clicked:
                for prefix, value in (("run_analysis", analysis.strip()), ("run_matrix", matrix.strip()), ("run_engine", engine_label),
                                      ("report_analysis", analysis.strip()), ("report_matrix", matrix.strip()), ("report_family", engine_label)):
                    st.session_state[f"{prefix}:{cohort.config_file}"] = value
                st.success("Design saved. Subject order, matrix, contrasts and inclusion audit are ready.")
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
    result = st.session_state.get("design_result")
    labels = st.session_state.get("design_result_labels")
    if result and labels == (cohort.name, analysis, matrix) and st.session_state.get("design_result_signature") == signature:
        st.divider()
        st.subheader(f"Design preview · {analysis} / {matrix}")
        counts = st.columns(3)
        counts[0].metric("Included visits", len(result["design"]))
        counts[1].metric("Design columns", len(result["design"].columns))
        counts[2].metric("Rows excluded", len(result.get("dropped_rows", [])))
        for label, path in result.get("paths", {}).items():
            st.caption(f"{label}: {path}")
        if result.get("paths") and st.button("Use this design in an analysis", type="primary"):
            _go("Run analysis")


def _run(root: Path, registry: Path, cohort, store: JobStore) -> None:
    if not _cohort_required(cohort):
        return
    st.subheader("Choose the analysis")
    first, second = st.columns(2)
    with first:
        analysis = st.text_input("Analysis name", value="AllSubjects", key=f"run_analysis:{cohort.config_file}")
        engine_label = st.selectbox("Statistical engine", list(ENGINES), key=f"run_engine:{cohort.config_file}")
        mask_labels = [label for label, value in MASKS.items() if value != "population" or "MRSI" in cohort.modalities]
        population_default = cohort.values.get("MRSI_INPUT_MODE") == "bids"
        default_mask = 0 if population_default else mask_labels.index("Whole white and grey matter")
        mask_label = st.selectbox("Brain mask", mask_labels, index=default_mask,
                                  help="Quality population only applies the MRSI coverage/precomputed mask without a separate tissue restriction.")
    with second:
        matrix = st.text_input("Model name", value="MainEffect", key=f"run_matrix:{cohort.config_file}")
        modalities = cohort.modalities or list(MODALITIES)
        modality = st.selectbox("Modality", modalities)
        permutations = st.number_input("Permutations", min_value=1, max_value=1000000, value=500, step=100,
                                        help="Use a suitable permutation count for your study. Low counts are exploratory.")
    mode_label = st.selectbox("What should VLAD do?", list(MODES))
    with st.expander("Advanced analysis settings"):
        first, second = st.columns(2)
        with first:
            b0 = st.selectbox("MRSI field-strength mode", ["3", "7", "7as3"],
                               format_func=lambda value: {"3": "3 T", "7": "7 T", "7as3": "7 T using the 3 T metabolite set"}[value])
            quality = st.slider("Minimum valid subject coverage (%)", 1, 99, 68)
            metabolites = st.text_input("Restrict metabolites", placeholder="Leave empty for all configured metabolites")
            quotients = st.text_input("Metabolite quotients", placeholder="For example *onCrPCr")
        with second:
            sums = st.checkbox("Include metabolite sums and ratios", value=False)
            smooth_enabled = st.checkbox("Smooth analysis images", value=False)
            smooth = st.number_input("Smoothing sigma", min_value=0.1, max_value=20.0, value=1.0, step=0.1)
            logarithm = st.checkbox("Apply logarithmic transform", value=False)
            parallel = st.checkbox("Use the site's configured parallel scheduler", value=False,
                                    help="Leave off for ordinary local execution. Parallel FSL needs scheduler configuration.")
            overwrite_outputs = st.checkbox("Rerun an analysis that already has outputs", value=False,
                                             help="Allows the workflow to reuse or regenerate this analysis's outputs. Use a new analysis name to retain a separate run.")
    try:
        options = AnalysisOptions(
            cohort=cohort, analysis_name=analysis.strip(), matrix_name=matrix.strip(), modality=modality,
            engine=ENGINES[engine_label], mode=MODES[mode_label], permutations=int(permutations),
            mask=MASKS[mask_label], quality_threshold=quality,
            metabolites=_tokens(metabolites) or None, include_sums=sums,
            quotients=_tokens(quotients) or None, b0=b0, smooth=float(smooth) if smooth_enabled else None,
            logarithm=logarithm, parallel=parallel, overwrite_outputs=overwrite_outputs,
        )
        problems = validate_analysis(options)
        command = analysis_command(root, options, validate=False)
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(str(exc))
        _jobs_panel(store, cohort)
        return
    st.subheader("Analysis command")
    st.code(command_text(command, root, registry), language="bash")
    if problems:
        for problem in problems:
            st.warning(str(problem))
        st.info("Complete these steps to enable command execution. Use Design to create a matrix and population list.")
    else:
        st.success(f"Ready: {cohort.name} · {analysis} · {matrix} · {ENGINES[engine_label]} · {int(permutations)} permutations")
    submitted = st.button("Start analysis", type="primary", disabled=bool(problems) or bool(_active_jobs(store, cohort)), width="stretch")
    if submitted and command and not problems:
        _start_job(store, root, registry, command, f"{analysis} / {matrix} · {ENGINES[engine_label]}",
                   _job_metadata(cohort, "analysis", analysis.strip(), matrix.strip()))
    _jobs_panel(store, cohort)


@st.fragment(run_every=2.0)
def _jobs_panel(store: JobStore, cohort=None) -> None:
    st.divider()
    st.subheader("Activity")
    jobs = store.list()
    if cohort:
        jobs = [job for job in jobs if job.get("metadata", {}).get("workspace") in (None, str(cohort.global_dir))]
    if not jobs:
        st.caption("Jobs will appear here when you prepare data, run an analysis, generate a report, or check the tools.")
        return
    lookup = {job["id"]: job for job in jobs}
    current = st.session_state.get("selected_job")
    selected_index = list(lookup).index(current) if current in lookup else 0
    if st.session_state.get("activity_job_selector") not in lookup:
        st.session_state["activity_job_selector"] = list(lookup)[selected_index]
    selected = st.selectbox("Job", list(lookup), index=selected_index,
                            format_func=lambda value: f"{lookup[value].get('label', value)} · {lookup[value].get('status', 'unknown')}",
                            key="activity_job_selector")
    try:
        job = store.get(selected)
    except (OSError, ValueError, RuntimeError) as exc:
        st.error(str(exc))
        return
    status = job.get("status", "unknown")
    if status in ACTIVE:
        st.info("Running in the background. You can change pages or reconnect while the job continues.")
    elif status == "succeeded":
        st.success("Completed successfully.")
    elif status == "stopped":
        st.warning("Stopped by the user.")
    else:
        st.error(f"Job {status}. Exit code: {job.get('returncode', 'not available')}. Open the detailed log if needed.")
    details, actions = st.columns([4, 1])
    with details:
        st.caption(f"Started: {job.get('started_at') or job.get('created_at', '—')}" +
                   (f" · Finished: {job['finished_at']}" if job.get("finished_at") else ""))
    with actions:
        if st.button("Stop job", disabled=status not in ACTIVE, width="stretch"):
            try:
                store.stop(selected)
                st.rerun()
            except (OSError, ValueError, RuntimeError) as exc:
                st.error(str(exc))
    if status in ACTIVE and job.get("metadata", {}).get("action") == "guided-workflow":
        try:
            progress_events = parse_progress(store.read_log(selected, tail_chars=200000))
        except (OSError, ValueError, RuntimeError):
            progress_events = []
        if progress_events:
            st.markdown("#### Metabolite progress")
            for event in progress_events:
                total = event.get("total")
                current = event.get("current", 0)
                detail = (" · " + event["detail"]) if event.get("detail") else ""
                label = f'{event["metabolite"]} · {event.get("stage", "Working")}{detail}'
                if total:
                    st.progress(min(1., current / total), text=f'{label} · {current}/{total}')
                else:
                    st.caption(f'{label} · permutation {current}')
    output_value = job.get("metadata", {}).get("output_dir")
    if output_value:
        output = Path(output_value)
        st.caption("Output folder: " + str(output))
        masks = sorted(output.glob("*/population_mask.nii.gz")) if output.is_dir() else []
        if masks:
            st.success(f"{len(masks)} population mask{'s' if len(masks) != 1 else ''} saved.")
            st.dataframe(pd.DataFrame({"Metabolite": [path.parent.name for path in masks],
                                       "Population mask": [str(path) for path in masks]}),
                         hide_index=True, width="stretch")
    if st.toggle("Show detailed log", value=False, key=f"activity_log_visible:{selected}"):
        try:
            log = store.read_log(selected, tail_chars=30000)
            st.code(log or "Waiting for output…", language="text")
        except (OSError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if status in ACTIVE and job.get("metadata", {}).get("interactive"):
        with st.expander("Respond to a workflow question"):
            st.caption("If the workflow asks a question in its output, send the answer here.")
            with st.form(f"job_input:{selected}", clear_on_submit=True):
                response = st.text_input("Answer")
                send = st.form_submit_button("Send answer")
            if send:
                try:
                    store.send_input(selected, response)
                    st.success("Answer sent.")
                except (OSError, ValueError, RuntimeError) as exc:
                    st.error(str(exc))
    with st.expander("Job command"):
        st.code(shlex.join(job.get("command", [])), language="bash")


def _reports(root: Path, registry: Path, cohort, store: JobStore) -> None:
    if not _cohort_required(cohort):
        return
    reports = list_reports(cohort)
    if reports:
        selected = st.selectbox("Completed report", [str(path) for path in reports],
                                format_func=lambda value: Path(value).name, key="report_selection")
        path = Path(selected)
        st.caption(f"Updated {datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')} · {path.parent}")
        if path.stat().st_size <= 30 * 1024 * 1024:
            markup = path.read_text(encoding="utf-8")
            first, second, third = st.columns(3)
            first.download_button("Download HTML report", markup, file_name=path.name, mime="text/html", width="stretch")
            metadata = path.with_suffix(".json")
            if metadata.is_file() and metadata.stat().st_size <= 30 * 1024 * 1024:
                second.download_button("Download statistical JSON", metadata.read_bytes(), file_name=metadata.name,
                                       mime="application/json", width="stretch")
                try:
                    manifest = json.loads(metadata.read_text())
                    outputs = manifest.get("outputs") if isinstance(manifest, dict) else None
                    feather_path = Path(outputs.get("feather", "")) if isinstance(outputs, dict) else Path("")
                    if feather_path.is_file() and feather_path.stat().st_size <= 50 * 1024 * 1024:
                        third.download_button("Download merged data", feather_path.read_bytes(), file_name=feather_path.name,
                                              mime="application/octet-stream", width="stretch")
                except (OSError, ValueError, TypeError):
                    pass
            components.html(markup, height=950, scrolling=True)
        else:
            st.info(f"This report is too large for an inline preview. Open the saved file: {path}")
    else:
        st.info("No HTML reports are available yet. Run an analysis or generate a report from existing extracted results below.")
    with st.expander("Generate a report from existing results", expanded=not bool(reports)):
        first, second = st.columns(2)
        with first:
            analysis = st.text_input("Analysis name", value="AllSubjects", key=f"report_analysis:{cohort.config_file}")
            kind_label = st.selectbox("Report family", list(ENGINES), key=f"report_family:{cohort.config_file}")
        with second:
            matrix = st.text_input("Model name", value="MainEffect", key=f"report_matrix:{cohort.config_file}")
            modality = st.selectbox("Report modality", cohort.modalities or list(MODALITIES))
            permutations = st.number_input("Permutations used", min_value=1, max_value=1000000, value=500)
        try:
            command = report_command(root, cohort, analysis.strip(), matrix.strip(), kind=ENGINES[kind_label],
                                     modality=modality.lower(), permutations=int(permutations))
            st.subheader("Report command")
            st.code(command_text(command, root, registry), language="bash")
            if st.button("Generate report", type="primary", disabled=bool(_active_jobs(store, cohort))):
                _start_job(store, root, registry, command, f"Report · {analysis} / {matrix}",
                           _job_metadata(cohort, "report", analysis.strip(), matrix.strip()))
        except (OSError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
    _jobs_panel(store, cohort)


def main() -> None:
    st.set_page_config(page_title="VLAD · Analysis workspace", page_icon="🧠", layout="wide", initial_sidebar_state="expanded")
    try:
        root = project_root()
        bootstrap_state_dir = Path(os.environ.get("VLAD_GUI_STATE_DIR", root / ".local/gui")).expanduser().resolve()
        from vlad.gui.settings import load_gui_settings
        gui_settings = load_gui_settings(bootstrap_state_dir)
        state_dir = Path(gui_settings["settings_dir"])
        store = _job_store(str(state_dir))
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(f"Could not open the VLAD workspace: {exc}")
        st.stop()
    if "global_bids_dir" not in st.session_state:
        st.session_state["global_bids_dir"] = gui_settings.get("bids_dir", "")
    if "global_theme" not in st.session_state:
        st.session_state["global_theme"] = gui_settings.get("theme", "Light")
    if "global_settings_dir" not in st.session_state:
        st.session_state["global_settings_dir"] = gui_settings.get("settings_dir", str(state_dir))
    apply_theme(st.session_state["global_theme"])
    with st.sidebar.popover("⚙ Global settings", use_container_width=True):
        st.selectbox("Theme", ["Light", "Dark"], key="global_theme")
        st.caption("The BIDS folder is the starting location for every Browse dialog.")
        path_input(
            "Main BIDS directory", key="global_bids_dir", kind="directory", inline=False,
            placeholder="/data/MyProject", help="Saved on this machine and reused after VLAD restarts."
        )
        path_input(
            "Local VLAD GUI data folder", key="global_settings_dir", kind="directory",
            allow_new=True, inline=False, help="Stores preferences and background-job history. A new folder is created when needed."
        )
        st.caption("Changing this folder switches future job history; the previous folder is left untouched.")
        st.button(
            "Save global settings", type="primary", use_container_width=True,
            on_click=_save_global_gui_settings, args=(bootstrap_state_dir,),
        )
        if notice := st.session_state.pop("global_settings_notice", None):
            st.success(notice)
        if error := st.session_state.pop("global_settings_error", None):
            st.error(error)
    interface = st.sidebar.selectbox("Interface", ["Guided MRSI workflow", "Existing studies"], key="interface_mode")
    if interface == "Guided MRSI workflow":
        from vlad.gui.wizard import render
        render(bids_dir=st.session_state.get("global_bids_dir", ""))
        return
    with st.sidebar:
        st.markdown("## VLAD")
        st.caption("Voxel-based analysis, from study to report.")
        if st.session_state.get("pending_page") in PAGES:
            st.session_state["navigation"] = st.session_state.pop("pending_page")
        page = st.radio("Workspace", PAGES, key="navigation", label_visibility="collapsed")
        with st.expander("Workspace settings"):
            registry_value = path_input("Cohort registry", value=str(registry_path(root)), key="registry_path", kind="savefile", allow_new=True, inline=False,
                                           help="A local text file listing the registered cohort configurations.")
            st.caption("Files and jobs stay on the machine running this app.")
        try:
            registry = _path(registry_value)
            cohorts = list_cohorts(registry)
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
            registry, cohorts = registry_path(root), []
        cohort_map = {str(item.config_file): item for item in cohorts}
        selection_key = "cohort_selection:" + str(registry)
        pending = st.session_state.pop("pending_cohort", None)
        if pending in cohort_map:
            st.session_state[selection_key] = pending
        if cohort_map:
            selected = st.selectbox("Cohort", list(cohort_map), key=selection_key,
                                    format_func=lambda value: cohort_map[value].name)
            cohort = cohort_map[selected]
            st.caption(str(cohort.global_dir))
        else:
            cohort = None
            st.info("Add your first study in Study setup.")
        st.divider()
        st.caption("Python reports · FSL analyses")
    hero(page, cohort.name if cohort else None)
    try:
        if page == "Overview":
            _overview(root, registry, cohorts, cohort, store)
        elif page == "Study setup":
            _setup(root, registry, cohort, store, state_dir)
        elif page == "Design":
            _design(root, cohort, store)
        elif page == "Run analysis":
            _run(root, registry, cohort, store)
        else:
            _reports(root, registry, cohort, store)
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
