"""python -m benchmark.install: install the pinned Chromium in project-local cache."""
import subprocess
import sys

subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
