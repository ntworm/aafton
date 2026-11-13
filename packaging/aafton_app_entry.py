"""Entry point of the window program of aafton.

The window is a command of the same line: the program calls it with the word
"app" before the arguments of the user.
"""

import sys

from aafton.cli import run_cli

raise SystemExit(run_cli(["app", *sys.argv[1:]]))
