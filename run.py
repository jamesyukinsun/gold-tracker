#!/usr/bin/env python
"""Convenience launcher.

    python run.py brief
    python run.py watch --interval 30
    python run.py dashboard

Equivalent to `python -m goldtrack ...`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from goldtrack.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
