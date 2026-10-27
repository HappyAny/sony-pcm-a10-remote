"""Build the self-contained, single-file Windows desktop."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name('build_portable.py')), run_name='__main__')
