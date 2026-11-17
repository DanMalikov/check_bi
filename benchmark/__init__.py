"""Local browser runner; no credentials or telemetry are written to reports."""
import os
from pathlib import Path

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).resolve().parent.parent / ".cache" / "ms-playwright"))
