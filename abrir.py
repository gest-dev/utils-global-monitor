#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    sys.exit(subprocess.run([sys.executable, str(Path(__file__).resolve().parent / 'extension/monitor.py'), *sys.argv[1:]]).returncode)
