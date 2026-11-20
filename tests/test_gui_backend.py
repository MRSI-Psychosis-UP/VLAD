"""Browser configuration, safe argv and real local job lifecycle checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from vlad.gui.cohorts import (
    Cohort,
    list_cohorts,
    load_cohort,
    read_assignments,
    register_cohort,
    save_cohort,
)
from vlad.gui.commands import (
    AnalysisOptions,
    analysis_command,
    preparation_command,
    report_command,
    validate_analysis,
)
from vlad.gui.jobs import ACTIVE, FINAL, JobStore


ROOT = Path(__file__).resolve().parents[1]


class CohortSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vlad-gui-settings-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry = self.base / "cohorts.txt"
        self.reference = self.base / "anatomical reference.nii.gz"
        self.reference.write_bytes(b"test configuration reference")
        self.subjects = self.base / "all subjects.txt"
        self.subjects.write_text("001_V1\n001_V2\n002_V1\n002_V2\n")
        self.inputs = self.base / "input images"
        self.inputs.mkdir()
        self.exports = self.base / "report exports"

    def save(self, name="Study", directory=None, **changes):
        arguments = dict(registry=self.registry, name=name,
                         global_dir=directory or self.base / "study",
                         reference_image=self.reference, subjects_file=self.subjects,
                         exports_dir=self.exports, input_dir=self.inputs,
                         metabolites=["NAA", "CrPCr"], masks=[])
        arguments.update(changes)
        return save_cohort(**arguments)

    def test_listing_configuration_never_executes_shell_commands(self):
        root = self.base / "existing study"
        root.mkdir()
        marker = self.base / "shell must not run"
        config = root / "config_global.sh"
        config.write_text(
            f"COHORT=Safe\nGLOBAL_DIR={shlex.quote(str(root))}\n"
            f"touch {shlex.quote(str(marker))}\n"
            f"BAD=$(touch {shlex.quote(str(marker))})\n"
            f"ALSO_BAD=`touch {shlex.quote(str(marker))}`\n"
        )
        self.registry.write_text(str(config) + "\n")
        cohorts = list_cohorts(self.registry)
        self.assertEqual(cohorts[0].name, "Safe")
        self.assertTrue(cohorts[0].warnings)
        self.assertNotIn("BAD", cohorts[0].values)
        self.assertFalse(marker.exists())

    def test_saved_paths_with_quotes_and_literal_dollars_round_trip(self):
        directory = self.base / "study 'quoted' $LITERAL"
        mask = self.base / "mask 'quoted' $MASK.nii.gz"
        mask.write_bytes(b"mask")
        cohort = self.save(directory=directory, masks=[str(mask)])
        self.assertEqual(cohort.global_dir, directory)
        reloaded = load_cohort(cohort.config_file)
        self.assertEqual(reloaded.global_dir, directory)
        self.assertEqual(reloaded.values["NII_DIR_MRSI"], str(self.inputs))
        self.assertEqual(reloaded.values["MRSI_MASK_LIST"], str(mask))
        self.assertEqual(reloaded.values["METAB"], ["NAA", "CrPCr"])
        self.assertEqual(reloaded.modalities, ["MRSI"])

    def test_literal_arrays_and_known_variable_expansion_follow_shell_quoting(self):
        config = self.base / "literal.sh"
        config.write_text(
            f"GLOBAL_DIR={shlex.quote(str(self.base))}\n"
            "METAB=(NAA 'Cr PCr' '$HOME-literal')\n"
            'INPUT="${GLOBAL_DIR}/images"\n'
            "LITERAL='$HOME/not-expanded'\n"
        )
        values, notes = read_assignments(config)
        self.assertEqual(values["METAB"], ["NAA", "Cr PCr", "$HOME-literal"])
        self.assertEqual(values["INPUT"], str(self.base / "images"))
        self.assertEqual(values["LITERAL"], "$HOME/not-expanded")
        self.assertFalse(notes)

    def test_registry_ignores_duplicates_and_retains_unavailable_entries(self):
        cohort = self.save()
        before = self.registry.read_text()
        register_cohort(self.registry, cohort.config_file)
        self.assertEqual(self.registry.read_text(), before)
        missing = self.base / "missing/config_global.sh"
        self.registry.write_text("# Cohorts\n\n" + before + before + str(missing))
        entries = list_cohorts(self.registry)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].config_file, cohort.config_file)
        self.assertTrue(entries[1].warnings)

    def test_duplicate_name_is_rejected_before_another_studys_files_change(self):
        self.save("StudyA", self.base / "study-a")
        existing = self.save("StudyB", self.base / "study-b")
        config = existing.config_file
        modality = existing.global_dir / "config_mrsi.sh"
        saved = (config.read_bytes(), modality.read_bytes(), self.registry.read_bytes())
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.save("StudyA", existing.global_dir, overwrite=True)
        self.assertEqual((config.read_bytes(), modality.read_bytes(), self.registry.read_bytes()), saved)

    def test_existing_settings_require_explicit_overwrite(self):
        cohort = self.save()
        config = cohort.config_file
        before = config.read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.save()
        self.assertEqual(config.read_bytes(), before)
        config.write_text(config.read_text() + "ADVANCED_SETTING='keep me'\n")
        updated = self.save(overwrite=True)
        self.assertEqual(updated.values["ADVANCED_SETTING"], "keep me")


class BrowserCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vlad-gui-command-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        root = self.base / "study"
        (root / "Listes").mkdir(parents=True)
        self.results = root / "Results/Population"
        self.results.mkdir(parents=True)
        config = root / "config_global.sh"
        config.write_text(f"COHORT=Study\nGLOBAL_DIR={root}\n")
        (root / "config_mrsi.sh").write_text("MRSI_FILES_VALIDATED=(NAANAAG_signal_filtbiharmonic_pvcorr)\n")
        (root / "Listes/List_Population.txt").write_text("001_V1\n001_V2\n002_V1\n002_V2\n")
        (self.results / "Model.mat").write_text("/NumWaves 2\n/NumPoints 4\n/Matrix\n1 .2\n1 .3\n1 .4\n1 .5\n")
        (self.results / "Model.con").write_text("/NumWaves 2\n/NumContrasts 2\n/Matrix\n0 1\n0 -1\n")
        self.cohort = Cohort("Study", config, root,
                             values={"MRSI_FILES_VALIDATED": ["NAANAAG_signal_filtbiharmonic_pvcorr"],
                                     "DIR_RANALYSES": str(root / "reports with 'quotes'")},
                             modalities=["MRSI"])

    def options(self, **changes):
        arguments = dict(cohort=self.cohort, analysis_name="Population", matrix_name="Model")
        arguments.update(changes)
        return AnalysisOptions(**arguments)

    def test_analysis_argv_preserves_selectors_and_uses_noninteractive_execution(self):
        args = analysis_command(ROOT, self.options(metabolites=["NAA", "CrPCr"],
                                                   quotients=["*onCrPCr"], include_sums=True,
                                                   smooth=1.5, logarithm=True))
        self.assertEqual(args[:4], [sys.executable, "-m", "vlad", "run"])
        self.assertIn("--non-interactive", args)
        self.assertIn("--noparallel", args)
        self.assertEqual(args[args.index("--quotients") + 1], "*onCrPCr")
        self.assertEqual(args[args.index("--metabs") + 1:args.index("--metabs") + 3], ["NAA", "CrPCr"])
        self.assertIn("--sum", args)
        self.assertNotIn("--remakeall", args)

    def test_option_like_labels_cannot_inject_destructive_workflow_flags(self):
        for changes in ({"metabolites": ["--remakeall"]}, {"quotients": ["--confirmation"]},
                        {"analysis_name": "Study;touch-marker"}, {"matrix_name": "../Model"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                analysis_command(ROOT, self.options(**changes))

    def test_missing_designs_and_missing_longitudinal_subject_file_fail_preflight(self):
        self.assertEqual(validate_analysis(self.options()), [])
        self.assertTrue(any("Model.sub" in error for error in validate_analysis(self.options(engine="swe"))))
        (self.results / "Model.con").unlink()
        self.assertTrue(any("Model.con" in error for error in validate_analysis(self.options())))

    def test_saved_but_unprepared_modality_does_not_launch_analysis(self):
        self.cohort.values["MRSI_FILES_VALIDATED"] = []
        (self.cohort.global_dir / "config_mrsi.sh").write_text("MRSI_FILES_VALIDATED=()\n")
        self.assertTrue(validate_analysis(self.options()))
        with self.assertRaises(ValueError):
            analysis_command(ROOT, self.options())

    def test_existing_outputs_need_explicit_rerun_and_report_paths_stay_single_arguments(self):
        (self.results / "rdm_existing.nii.gz").write_bytes(b"generated output")
        with self.assertRaisesRegex(ValueError, "outputs"):
            analysis_command(ROOT, self.options())
        self.assertIn("--non-interactive", analysis_command(ROOT, self.options(overwrite_outputs=True)))
        args = report_command(ROOT, self.cohort, "Population", "Model")
        self.assertEqual(args[args.index("--export-dir") + 1], self.cohort.values["DIR_RANALYSES"])
        self.assertEqual(args[args.index("--results-dir") + 1], str(self.results))
        prep = preparation_command(ROOT, self.cohort, "MRSI", b0="7", overwrite=True)
        self.assertIn("--non-interactive", prep)
        self.assertIn("--overwrite", prep)
        self.assertEqual(prep[prep.index("--config") + 1], str(self.cohort.config_file))


class PersistentJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vlad-gui-jobs-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = JobStore(self.base / "history")
        self.addCleanup(self.cleanup_jobs)

    def cleanup_jobs(self):
        for job_id, child in list(self.store._children.items()):
            try:
                self.store.stop(job_id)
            except (OSError, ValueError):
                pass
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)

    def wait_for(self, job_id, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.store.get(job_id)
            if predicate(job):
                return job
            time.sleep(.03)
        self.fail(f"Job did not reach expected state: {self.store.get(job_id)}\n{self.store.read_log(job_id)}")

    def test_jobs_survive_store_recreation_capture_logs_and_do_not_record_environment_secrets(self):
        secret = "test-secret-" + uuid.uuid4().hex
        environment = dict(os.environ, VLAD_TEST_PRIVATE_SECRET=secret)
        job = self.store.start([sys.executable, "-c", "print('completed analysis')"],
                               cwd=self.base, env=environment, label="Example")
        finished = self.wait_for(job["id"], lambda value: value["status"] in FINAL)
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["returncode"], 0)
        restored = JobStore(self.store.state_dir)
        self.assertEqual(restored.get(job["id"])["status"], "succeeded")
        self.assertIn("completed analysis", restored.read_log(job["id"]))
        saved = (self.store.state_dir / job["id"] / "job.json").read_text()
        self.assertNotIn(secret, saved)
        self.assertNotIn("VLAD_TEST_PRIVATE_SECRET", saved)
        self.assertNotIn("env", json.loads(saved))

    def test_failed_program_status_and_stderr_are_retained(self):
        job = self.store.start([sys.executable, "-c", "import sys; print('analysis failed',file=sys.stderr); sys.exit(19)"], cwd=self.base)
        finished = self.wait_for(job["id"], lambda value: value["status"] in FINAL)
        self.assertEqual((finished["status"], finished["returncode"]), ("failed", 19))
        self.assertIn("analysis failed", self.store.read_log(job["id"]))

    def test_single_line_input_reaches_waiting_process_without_shell_evaluation(self):
        job = self.store.start([sys.executable, "-c", "print('ready',flush=True); answer=input(); print('ANSWER='+answer,flush=True)"], cwd=self.base)
        self.wait_for(job["id"], lambda value: value["status"] == "running" and "ready" in self.store.read_log(job["id"]))
        with self.assertRaises(ValueError):
            self.store.send_input(job["id"], "one\ntwo")
        response = "$(touch should-not-exist) with spaces"
        self.store.send_input(job["id"], response)
        self.assertEqual(self.wait_for(job["id"], lambda value: value["status"] in FINAL)["status"], "succeeded")
        self.assertIn("ANSWER=" + response, self.store.read_log(job["id"]))
        self.assertFalse((self.base / "should-not-exist").exists())

    def test_active_workspace_collision_is_detected_across_store_instances(self):
        metadata = {"workspace": str(self.base / "study")}
        job = self.store.start([sys.executable, "-c", "import time; time.sleep(8)"], cwd=self.base, metadata=metadata)
        self.wait_for(job["id"], lambda value: value["status"] == "running")
        restored = JobStore(self.store.state_dir)
        with self.assertRaisesRegex(ValueError, "already using"):
            restored.start([sys.executable, "-c", "pass"], cwd=self.base, metadata=metadata)
        self.assertEqual(self.store.stop(job["id"])["status"], "stopped")

    def test_stop_ends_descendants_even_if_a_child_ignores_termination(self):
        ready = self.base / "descendant-ready"
        survived = self.base / "descendant-survived"
        grandchild = (
            "import os,signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            f"Path({str(ready)!r}).write_text(str(os.getpid())); "
            "counter=0\n"
            "while True:\n"
            " time.sleep(.05)\n"
            " counter+=1\n"
            f" Path({str(survived)!r}).write_text(str(counter))\n"
        )
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(grandchild) + "]); time.sleep(8)"
        job = self.store.start([sys.executable, "-c", parent], cwd=self.base)
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(ready.exists(), self.store.read_log(job["id"]))
        descendant = int(ready.read_text())
        def stop_descendant():
            try:
                os.kill(descendant, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(stop_descendant)
        stopped = self.store.stop(job["id"])
        self.assertEqual(stopped["status"], "stopped")
        # The grace period permits progress before stop() returns. It must not
        # permit a SIGTERM-ignoring descendant to continue after cancellation.
        time.sleep(.1)
        progress = survived.read_text() if survived.exists() else None
        time.sleep(.25)
        self.assertEqual(survived.read_text() if survived.exists() else None, progress,
                         "Cancellation left a descendant process running")

    def test_job_lookup_cannot_escape_the_history_directory(self):
        with self.assertRaises(ValueError):
            self.store.get("../outside")
        with self.assertRaises(ValueError):
            self.store.start([sys.executable, "bad\x00argument"], cwd=self.base)


if __name__ == "__main__":
    unittest.main()
