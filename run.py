"""One command to start everything:  python run.py

Starts the Knowledge Brain API (http://127.0.0.1:8000, docs at /docs) and the
Streamlit app (http://localhost:8501). Ctrl+C stops both.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
API_PORT = int(os.getenv("KB_PORT", "8000"))
UI_PORT = int(os.getenv("UI_PORT", "8501"))


def wait_for_api(timeout_s: float = 90) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{API_PORT}/health", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "KB_API_URL": f"http://127.0.0.1:{API_PORT}",
           "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "task1_knowledge_brain.app.api:app",
                            "--host", "127.0.0.1", "--port", str(API_PORT)], cwd=ROOT, env=env)
    print(f"Starting Knowledge Brain API on http://127.0.0.1:{API_PORT} ...")
    if not wait_for_api():
        print("The API did not start. See the log above.")
        api.terminate()
        return 1
    print(f"API ready (docs: http://127.0.0.1:{API_PORT}/docs). Starting the app on http://localhost:{UI_PORT} ...")
    ui = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(UI_PORT),
                           "--server.headless", "true"], cwd=ROOT, env=env)
    try:
        while api.poll() is None and ui.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for proc in (ui, api):
            if proc.poll() is None:
                proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
