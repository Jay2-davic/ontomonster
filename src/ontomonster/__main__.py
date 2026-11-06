# SPDX-License-Identifier: BSD-3-Clause
"""Start the OntoMonster app with this command: python -m ontomonster

The command starts a blank session. No project opens automatically. The
command then opens Firefox at the URL of the app.
"""

import argparse
import threading
import webbrowser

import uvicorn

from . import app as _app


def _open_browser(url: str) -> None:
    """Open the app in Firefox. If Firefox is not available, use the
    default browser of the system.
    """
    for name in ("firefox", "firefox-browser", "mozilla"):
        try:
            webbrowser.get(name).open_new(url)
            return
        except Exception:  # The OS has no registered name for Firefox.
            continue
    try:
        webbrowser.open_new(url)  # Use the default browser.
    except Exception:
        pass


def _kill_port(port: int) -> bool:
    """Kill process using the specified port. Returns True if successful."""
    import platform
    import subprocess

    system = platform.system()

    try:
        if system == "Windows":
            # Find PID using the port
            result = subprocess.run(
                f'netstat -ano | findstr :{port}',
                shell=True,
                capture_output=True,
                text=True
            )
            if result.stdout:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if 'LISTENING' in line:
                        pid = line.split()[-1]
                        # Kill the process
                        subprocess.run(f'taskkill /PID {pid} /F', shell=True, capture_output=True)
                        return True
        else:  # Linux/macOS
            # Find PID using the port
            result = subprocess.run(
                f'lsof -ti:{port}',
                shell=True,
                capture_output=True,
                text=True
            )
            if result.stdout:
                pid = result.stdout.strip()
                # Kill the process
                subprocess.run(f'kill -9 {pid}', shell=True, capture_output=True)
                return True
    except Exception:
        return False

    return False


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="ontomonster", description="Run the OntoMonster web app."
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open a browser window on start",
    )
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}"

    # Check if port is available before starting
    import socket
    import time

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((args.host, args.port))
        sock.close()
    except OSError:
        print(f"Port {args.port} is already in use. Closing existing process...")
        if _kill_port(args.port):
            print(f"Successfully closed port {args.port}.")
            time.sleep(0.5)  # Give the OS time to release the port
        else:
            print(f"Could not automatically close port {args.port}.")
            print(f"Try using a different port: ontomonster --port {args.port + 1}")
            return

    print(f"OntoMonster -> {url}")
    if not args.no_browser:
        # Start the browser a short time after the server starts.
        threading.Timer(0.5, _open_browser, args=(url,)).start()
    uvicorn.run(_app.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
