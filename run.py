"""
Launcher for the Satellite Change Detection web app.

Ways to run this:
  - Python IDLE:  open this file, then Run > Run Module (F5)
  - Double-click: works if .py files are associated with Python
  - Terminal:     python run.py

It starts the FastAPI server with uvicorn and opens your browser.
Stop the server with Ctrl+C in the terminal (or close the IDLE shell window).
"""
import os
import sys
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 8000


def _open_browser_later(url: str):
    # Give the server a moment to start, then open the browser once.
    time.sleep(1.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    # Make sure `import app.main` works no matter where this is launched from.
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    if here not in sys.path:
        sys.path.insert(0, here)

    # Local runs use DDA dev UI + folder library unless already set
    os.environ.setdefault("APP_MODE", "dda")

    try:
        import uvicorn
    except ImportError:
        print("ERROR: dependencies are not installed.")
        print("Run this first:  pip install -r requirements.txt")
        sys.exit(1)

    url = f"http://{HOST}:{PORT}"
    print("Starting Satellite Change Detection...")
    print(f"Open in your browser:  {url}")
    print("Press Ctrl+C to stop.\n")

    threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()

    # reload=False keeps it simple and IDLE-friendly. For live-reload during
    # development, run instead:  uvicorn app.main:app --reload --port 8000
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
