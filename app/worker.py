"""In-process background worker. The queue lives in memory; the database is the source of truth.

A crash loses the queue; the startup sweep marks those analyses as interrupted_by_restart.
"""

import logging
import queue
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, run: Callable[[str], None]):
        self._run = run
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="analysis-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def submit(self, analysis_id: str) -> None:
        self._queue.put(analysis_id)

    def _loop(self) -> None:
        while (analysis_id := self._queue.get()) is not None:
            try:
                self._run(analysis_id)
            except Exception:  # the runner records failures itself; this is a last line of defence
                log.exception("worker crashed on %s", analysis_id)
