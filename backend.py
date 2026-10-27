"""Dedicated headless entry point for the Fluent desktop application."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from pcma10_remote.ipc import run

if __name__ == "__main__":
    run(demo="--demo" in sys.argv)
