"""Entry point of the command line program of aafton.

PyInstaller runs this file as a script, so the import is absolute.
"""

import sys

from aafton.cli import run_cli

raise SystemExit(run_cli(sys.argv[1:]))
