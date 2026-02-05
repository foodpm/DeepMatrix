import asyncio
import os
import socket
import sys
import threading
import time

import uvicorn

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.main import app


def _is_port_free(host: str, port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, port))
        s.close()
        return True
    except OSError:
        return False


def _pick_port(host: str, preferred_port: int) -> int:
    port = max(1, int(preferred_port))
    for _ in range(50):
        if _is_port_free(host, port):
            return port
        port += 1
    return 0


def _wait_http_up(host: str, port: int, timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + max(0.1, float(timeout_seconds))
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except Exception:
            time.sleep(0.15)
    return False


def main():
    host = "127.0.0.1"
    preferred_port = int(os.environ.get("SHELF_PORT") or 8000)
    port = _pick_port(host, preferred_port)
    if port <= 0:
        raise RuntimeError("no available port")

    os.environ["SHELF_NO_BROWSER"] = "1"
    url = f"http://{host}:{port}/"

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not _wait_http_up(host, port, timeout_seconds=20.0):
        server.should_exit = True
        server_thread.join(timeout=2.0)
        raise RuntimeError("server did not start")

    try:
        import webview
    except Exception:
        import webbrowser

        webbrowser.open(url)
        server_thread.join()
        return

    webview.create_window("深维工坊 DeepMatrix", url=url, width=1280, height=800)
    try:
        webview.start()
    finally:
        server.should_exit = True
        server_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
