"""Launcher script - run the desktop application.

Usage:
    python run.py
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path (works from any cwd)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from main import main  # noqa: E402

if __name__ == "__main__":
    main()
