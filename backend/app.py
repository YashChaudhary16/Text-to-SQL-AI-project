"""Vercel entrypoint: exposes the FastAPI app.

Vercel's Python runtime looks for `app` in app.py at the project root (backend/). Adding src/ to
the path makes this work whether or not the build installs the `sqlagent` package itself.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sqlagent.api.app import app  # noqa: E402

__all__ = ["app"]
