import multiprocessing
import os
import signal
import sys
import time


def init_child_process_prelude() -> None:
    """
    Configure multiprocessing spawn child processes during early module import.

    Spawn children (e.g. RenderWorker) re-import the main module during multiprocessing's
    child preparation, before the bootstrap reaches the target function.
    ``current_process()._inheriting`` is set by ``spawn_main.prepare()`` only while the
    child is importing the main module; the application's own import never sees it set.

    This prelude:
    1. Ignores SIGINT so children are not killed prematurely by Ctrl+C before the parent
       can manage their lifecycle (see RESEARCH.md §1.23).
    2. Redirects stderr/stdout to ``PDFATLAS_CHILD_STDERR_LOG`` so spawn/import crashes
       (e.g. WebKitGTK) are diagnosable in logs instead of silent child exits.
    """
    if getattr(multiprocessing.current_process(), "_inheriting", False):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        child_log = os.environ.get("PDFATLAS_CHILD_STDERR_LOG")
        if child_log:
            try:
                f = open(child_log, "a", buffering=1)
                sys.stderr = f
                sys.stdout = f
                f.write(f"\n=== render child pid={os.getpid()} at {time.time():.3f} ===\n")
            except Exception:
                pass
