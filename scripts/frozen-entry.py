"""PyInstaller entry point; all commands share the same executable."""

import multiprocessing
import sys

from sft.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
