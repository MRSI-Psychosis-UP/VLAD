"""Detached job supervisor. The browser process never owns the analysis lifetime."""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import time

from .jobs import read_job, timestamp, update_job


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    directory = args.job_dir.resolve()
    job = read_job(directory)
    stopped = False
    child = None
    descriptor = None

    def stop(signum, frame):
        nonlocal stopped
        stopped = True
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    current = update_job(directory, pid=os.getpid(), status="running", started_at=timestamp())
    if current["status"] != "running":
        return 0
    try:
        descriptor = os.open(directory / "stdin", os.O_RDWR)
        with (directory / "output.log").open("ab", buffering=0) as output:
            child = subprocess.Popen(job["command"], cwd=job["cwd"], stdin=descriptor,
                                     stdout=output, stderr=subprocess.STDOUT)
            update_job(directory, child_pid=child.pid)
            while child.poll() is None:
                update_job(directory, heartbeat=timestamp())
                time.sleep(.2)
            code = child.wait()
        update_job(directory, status="stopped" if stopped else ("succeeded" if code == 0 else "failed"),
                   returncode=code, finished_at=timestamp())
        return code if code >= 0 else 1
    except BaseException as exc:
        update_job(directory, status="stopped" if stopped else "failed", returncode=1,
                   finished_at=timestamp(), error=f"{type(exc).__name__}: {exc}")
        if child is not None and child.poll() is None:
            child.terminate()
        return 1
    finally:
        if stopped:
            # Keep the verified process-group leader alive until the controller
            # escalates cancellation, including descendants ignoring SIGTERM.
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                time.sleep(.1)
        if descriptor is not None:
            os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
