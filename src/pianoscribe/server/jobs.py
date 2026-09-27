"""Background jobs for the viewer: adding a song runs the whole pipeline (minutes), one at a time."""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field


@dataclass
class Job:
    id: str
    label: str
    status: str = "queued"  # queued | running | done | error
    step: str = ""
    message: str = "waiting for the previous job"
    result: str | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    log: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "log"} | {"log": self.log[-12:]}


class JobRunner:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, label: str, fn: Callable[[Callable[[str, str], None]], str]) -> Job:
        """fn(progress) runs in the background and returns a result string (e.g. the song slug)."""
        job = Job(uuid.uuid4().hex[:10], label)
        self.jobs[job.id] = job
        self._queue.put((job, fn))
        return job

    def _work(self) -> None:
        while True:
            job, fn = self._queue.get()
            job.status, job.message = "running", "starting"

            def progress(step: str, message: str, job: Job = job) -> None:
                job.step, job.message = step, message
                job.log.append(f"[{step}] {message}")

            try:
                job.result = fn(progress)
                job.status, job.message = "done", "ready"
            except Exception as exc:  # report, don't kill the worker
                job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
                job.log.append(traceback.format_exc(limit=3))
