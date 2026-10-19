"""One command to start everything:  python run.py

Starts the Knowledge Brain API (http://127.0.0.1:8000, docs at /docs) and the
Streamlit app (http://localhost:8501 or Render $PORT). Ctrl+C stops both.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent

# Render sets PORT for the public web service; default to 8501 for local development
UI_PORT = int(os.getenv("PORT", os.getenv("UI_PORT", "8501")))
# Pick an internal API port that does not clash with the public UI port
_default_api_port = "8001" if UI_PORT == 8000 else "8000"
API_PORT = int(os.getenv("KB_PORT", _default_api_port))


def wait_for_api(url: str, timeout_s: float = 90) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def main() -> int:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
    }

    # If an external KB_API_URL is already provided (e.g. dual-service on Render),
    # use it directly without spawning a duplicate local API.
    external_kb_url = os.getenv("KB_API_URL", "").rstrip("/")
    run_local_api = os.getenv("START_API", "1") == "1" and not (
        external_kb_url and not any(h in external_kb_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))
    )

    api_proc: subprocess.Popen | None = None
    if run_local_api:
        api_url = f"http://127.0.0.1:{API_PORT}"
        env["KB_API_URL"] = api_url
        print(f"Starting Knowledge Brain API on {api_url} ...")
        api_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "task1_knowledge_brain.app.api:app",
             "--host", "127.0.0.1", "--port", str(API_PORT)],
            cwd=ROOT, env=env,
        )
        if not wait_for_api(api_url):
            print("The API did not start. See the log above.")
            api_proc.terminate()
            return 1
        print(f"API ready (docs: {api_url}/docs).")
    else:
        print(f"Using external Knowledge Brain API at: {external_kb_url}")
        env["KB_API_URL"] = external_kb_url

    print(f"Starting Streamlit app on port {UI_PORT} (0.0.0.0) ...")
    ui_cmd = [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.port", str(UI_PORT),
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    ui_proc = subprocess.Popen(ui_cmd, cwd=ROOT, env=env)

    procs = [p for p in (ui_proc, api_proc) if p is not None]
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
