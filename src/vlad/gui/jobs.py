"""Durable local jobs, independent of Streamlit reruns and browser sessions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from .cohorts import atomic_text, file_lock

ACTIVE = {"queued", "running"}
FINAL = {"succeeded", "failed", "stopped"}
PROGRESS_PREFIX = "VLAD_PROGRESS "


def parse_progress(log: str) -> list[dict]:
    """Return the latest structured progress event for each metabolite."""
    latest = {}
    for line in str(log or "").splitlines():
        marker = line.find(PROGRESS_PREFIX)
        if marker < 0:
            continue
        try:
            event = json.loads(line[marker + len(PROGRESS_PREFIX):])
            metabolite = str(event["metabolite"])
            current = int(event.get("current", 0))
            total = event.get("total")
            total = int(total) if total is not None else None
            if current < 0 or (total is not None and total < 0):
                continue
            event.update(metabolite=metabolite, current=current, total=total)
            latest[metabolite] = event
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return [latest[key] for key in sorted(latest, key=str.casefold)]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_job(directory: Path) -> dict:
    return json.loads((directory / "job.json").read_text())


def update_job(directory: Path, **changes) -> dict:
    with file_lock(directory / ".lock"):
        job = read_job(directory)
        if job.get("status") in FINAL and changes.get("status") in ACTIVE | FINAL:
            return job
        job.update(changes)
        job["updated_at"] = timestamp()
        atomic_text(directory / "job.json", json.dumps(job, indent=2) + "\n")
    return job


def worker_alive(pid: int | None, directory: Path) -> bool:
    if not pid or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        if Path(f"/proc/{pid}/cmdline").exists():
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
        else:
            command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=2).stdout
        return "vlad.gui.worker" in command and str(directory) in command
    except (OSError, subprocess.TimeoutExpired):
        return False


class JobStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._children = {}

    def _directory(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("Invalid job identifier.")
        path = self.state_dir / job_id
        if not (path / "job.json").is_file():
            raise ValueError("This job is no longer in the local history.")
        return path

    def start(self, command: list[str], *, cwd: Path, env: dict | None = None,
              label: str = "", metadata: dict | None = None) -> dict:
        if not command or any(not isinstance(value, str) or "\x00" in value for value in command):
            raise ValueError("A job needs a valid executable and literal argument list.")
        cwd = Path(cwd).resolve()
        if not cwd.is_dir():
            raise ValueError("The job working directory does not exist.")
        metadata = metadata or {}
        with file_lock(self.state_dir / ".start.lock"):
            for previous in self.list():
                if previous["status"] in ACTIVE and metadata.get("workspace") and previous.get("metadata", {}).get("workspace") == metadata["workspace"]:
                    raise ValueError("Another job is already using this study. Wait for it to finish or stop it first.")
            job_id = uuid.uuid4().hex
            directory = self.state_dir / job_id
            directory.mkdir(mode=0o700)
            os.mkfifo(directory / "stdin", mode=0o600)
            job = {"id": job_id, "label": label or Path(command[0]).name,
                   "status": "queued", "created_at": timestamp(), "updated_at": timestamp(),
                   "started_at": None, "finished_at": None, "returncode": None,
                   "pid": None, "command": command, "cwd": str(cwd), "metadata": metadata}
            atomic_text(directory / "job.json", json.dumps(job, indent=2) + "\n")
            environment = dict(os.environ if env is None else env)
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[2]), environment.get("PYTHONPATH"))))
            try:
                with (directory / "worker.log").open("ab") as log:
                    child = subprocess.Popen([sys.executable, "-m", "vlad.gui.worker", "--job-dir", str(directory)],
                                             env=environment, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                self._children[job_id] = child
                job = update_job(directory, pid=child.pid)
            except OSError as exc:
                update_job(directory, status="failed", finished_at=timestamp(), error=str(exc), returncode=1)
                raise ValueError(f"Unable to start the job: {exc}") from exc
        return job

    def get(self, job_id: str) -> dict:
        directory = self._directory(job_id)
        job = read_job(directory)
        child = self._children.get(job_id)
        if child is not None and child.poll() is not None:
            self._children.pop(job_id, None)
        if job["status"] in ACTIVE and job.get("pid") and not worker_alive(job["pid"], directory):
            job = update_job(directory, status="failed", finished_at=timestamp(), returncode=1,
                             error="The job runner exited unexpectedly. Check its log before retrying.")
        return job

    def list(self) -> list[dict]:
        jobs = []
        for path in self.state_dir.glob("*/job.json"):
            try:
                jobs.append(self.get(path.parent.name))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        return sorted(jobs, key=lambda job: job["created_at"], reverse=True)

    def read_log(self, job_id: str, tail_chars: int = 30000) -> str:
        directory = self._directory(job_id)
        output = []
        for name in ("output.log", "worker.log"):
            path = directory / name
            if path.exists():
                with path.open("rb") as handle:
                    handle.seek(max(0, path.stat().st_size - tail_chars))
                    text = handle.read().decode("utf-8", errors="replace")
                    if text.strip():
                        output.append(text)
        return "\n".join(output)[-tail_chars:]

    def send_input(self, job_id: str, text: str) -> None:
        directory = self._directory(job_id)
        if self.get(job_id)["status"] != "running":
            raise ValueError("This job is not waiting for input.")
        if "\x00" in text or "\n" in text or "\r" in text:
            raise ValueError("Send one response line at a time.")
        payload = (text + "\n").encode()
        try:
            descriptor = os.open(directory / "stdin", os.O_WRONLY | os.O_NONBLOCK)
            try:
                if len(payload) > os.fpathconf(descriptor, "PC_PIPE_BUF"):
                    raise ValueError("The response is too long for one input line.")
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
        except (OSError, BlockingIOError) as exc:
            raise ValueError("The input channel is not ready. Wait a moment and retry.") from exc

    def stop(self, job_id: str) -> dict:
        directory = self._directory(job_id)
        job = self.get(job_id)
        if job["status"] not in ACTIVE:
            return job
        pid = job.get("pid")
        if worker_alive(pid, directory):
            try:
                if os.getpgid(pid) != pid:
                    raise ValueError("The runner's process group changed; stop it locally rather than cancelling unrelated processes.")
                os.killpg(pid, signal.SIGTERM)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if not worker_alive(pid, directory):
                        break
                    time.sleep(.05)
                if worker_alive(pid, directory):
                    os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return update_job(directory, status="stopped", returncode=-signal.SIGTERM, finished_at=timestamp())
