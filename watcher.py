"""
Taal Cafe – File Watcher
========================
Monitors the Sales/, Costs/, and Hours/ folders for new or changed .xlsx files.
When a change is detected it waits 5 seconds (debounce) then runs the pipeline.

Usage
-----
  python3 watcher.py          # start watching (Ctrl-C to stop)
  python3 watcher.py --once   # process any pending changes and exit
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

BASE = Path(__file__).parent
WATCH_DIRS = [BASE / "Sales", BASE / "Costs", BASE / "Hours", BASE / "Product Sales"]
PIPELINE   = BASE / "pipeline.py"
PYTHON     = sys.executable

DEBOUNCE_SECONDS = 5   # wait this long after last event before running


def ts():
    return datetime.now().strftime("%H:%M:%S")


def run_pipeline():
    print(f"\n[{ts()}] ▶ Running pipeline...")
    result = subprocess.run(
        [PYTHON, str(PIPELINE)],
        cwd=str(BASE),
        capture_output=False,
    )
    if result.returncode == 0:
        print(f"[{ts()}] ✓ Pipeline complete.")
    else:
        print(f"[{ts()}] ✗ Pipeline exited with code {result.returncode}.")
    print(f"\n[{ts()}] Watching for changes  (Ctrl-C to stop)...\n")


class XlsxHandler(FileSystemEventHandler):
    def __init__(self):
        self._pending = False
        self._last_event_time = 0

    def _is_relevant(self, path: str) -> bool:
        p = Path(path)
        return p.suffix.lower() in (".xlsx", ".csv") and not p.name.startswith("~$")

    def on_any_event(self, event):
        if event.is_directory:
            return
        src = getattr(event, "src_path", "")
        dst = getattr(event, "dest_path", "")
        if self._is_relevant(src) or self._is_relevant(dst):
            self._pending = True
            self._last_event_time = time.time()
            print(f"[{ts()}] File event: {Path(src).name}  (waiting {DEBOUNCE_SECONDS}s…)")

    def check_and_run(self):
        """Call periodically — fires pipeline once debounce window has elapsed."""
        if (
            self._pending
            and (time.time() - self._last_event_time) >= DEBOUNCE_SECONDS
        ):
            self._pending = False
            run_pipeline()


def watch():
    handler  = XlsxHandler()
    observer = Observer()
    for d in WATCH_DIRS:
        d.mkdir(exist_ok=True)
        observer.schedule(handler, str(d), recursive=False)

    observer.start()
    print(f"[{ts()}] Watcher started. Monitoring:")
    for d in WATCH_DIRS:
        print(f"  {d}")
    print(f"\nDropping new .xlsx files will trigger the pipeline after {DEBOUNCE_SECONDS}s.\n")
    print(f"[{ts()}] Watching for changes  (Ctrl-C to stop)...\n")

    try:
        while True:
            handler.check_and_run()
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print(f"\n[{ts()}] Watcher stopped.")
    observer.join()


def once():
    """Check for pending changes and run if any, then exit."""
    result = subprocess.run(
        [PYTHON, str(PIPELINE), "--status"],
        cwd=str(BASE),
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    if "up to date" in output:
        print(f"[{ts()}] No changes detected. Nothing to do.")
    else:
        print(output)
        run_pipeline()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Taal Cafe file watcher")
    parser.add_argument("--once", action="store_true",
                        help="Process pending changes once and exit")
    args = parser.parse_args()

    if args.once:
        once()
    else:
        watch()
