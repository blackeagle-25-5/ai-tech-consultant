"""One command to start everything:  python run.py

Starts the Knowledge Brain API (http://127.0.0.1:8000, docs at /docs) and the
Streamlit app (http://localhost:8501 or Render $PORT) in a unified, single-process
architecture optimized to fit easily within Render's 512MB RAM free tier limit.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# Limit thread pools to 1 to prevent high memory allocation on multi-core Render hosts
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Render sets PORT for the public web service; default to 8501 for local development
UI_PORT = int(os.getenv("PORT", os.getenv("UI_PORT", "8501")))
_default_api_port = "8001" if UI_PORT == 8000 else "8000"
API_PORT = int(os.getenv("KB_PORT", _default_api_port))


def _start_uvicorn() -> None:
    import uvicorn
    from task1_knowledge_brain.app.api import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=API_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


def main() -> int:
    external_kb_url = os.getenv("KB_API_URL", "").rstrip("/")
    run_local_api = os.getenv("START_API", "1") == "1" and not (
        external_kb_url and not any(h in external_kb_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))
    )

    if run_local_api:
        api_url = f"http://127.0.0.1:{API_PORT}"
        os.environ["KB_API_URL"] = api_url
        print(f"Starting Knowledge Brain API in background thread on {api_url} ...")
        t = threading.Thread(target=_start_uvicorn, daemon=True)
        t.start()
        time.sleep(1)
        print(f"API ready (docs: {api_url}/docs).")
    else:
        print(f"Using external Knowledge Brain API at: {external_kb_url}")
        os.environ["KB_API_URL"] = external_kb_url

    print(f"Starting Streamlit app on port {UI_PORT} (0.0.0.0) ...")
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.port",
        str(UI_PORT),
        "--server.address",
        "0.0.0.0",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.enableCORS",
        "false",
        "--server.enableXsrfProtection",
        "false",
    ]
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
