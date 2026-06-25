from __future__ import annotations

import argparse
import os
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def open_when_ready(url: str) -> None:
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Planline PDF-to-CAD local web app."
    )
    parser.add_argument("--host", default=os.environ.get("PLANLINE_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PLANLINE_PORT", "8765")),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the app in the default browser.",
    )
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser and args.host in {"127.0.0.1", "localhost"}:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(
        "pdf_plan_to_dwg.app.main:app",
        host=args.host,
        port=args.port,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()

