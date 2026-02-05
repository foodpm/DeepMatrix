import asyncio
import os
import socket
import sys
import threading
import time
import traceback
import subprocess

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


def _fatal_log_path() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Logs/DeepMatrix")
    elif os.name == "nt":
        base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), "DeepMatrix")
    else:
        base = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "deepmatrix")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "DeepMatrix.log")


def _write_fatal_log(err: BaseException) -> str:
    path = _fatal_log_path()
    try:
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write(f"{type(err).__name__}: {err}\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass
    return path


def _show_fatal_dialog(message: str) -> None:
    if sys.platform == "darwin":
        try:
            s = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            subprocess.run(["osascript", "-e", f'display dialog "{s}" buttons {{"OK"}} default button "OK" with title "DeepMatrix"'], check=False)
        except Exception:
            pass
        return
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "DeepMatrix", 0)
        except Exception:
            pass


def main() -> int:
    bind_host = str(os.environ.get("SHELF_HOST") or "").strip() or "0.0.0.0"
    local_host = "127.0.0.1"
    preferred_port = int(os.environ.get("SHELF_PORT") or 8000)
    port = _pick_port(bind_host, preferred_port)
    if port <= 0:
        raise RuntimeError("no available port")

    os.environ["SHELF_NO_BROWSER"] = "1"
    url = f"http://{local_host}:{port}/"

    config = uvicorn.Config(app, host=bind_host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not _wait_http_up(local_host, port, timeout_seconds=20.0):
        server.should_exit = True
        server_thread.join(timeout=2.0)
        raise RuntimeError("server did not start")

    try:
        import webview
    except Exception:
        import webbrowser

        webbrowser.open(url)
        server_thread.join()
        return 0

    webview.create_window("深维工坊 DeepMatrix", url=url, width=1280, height=800)
    try:
        webview.start()
        return 0
    finally:
        server.should_exit = True
        server_thread.join(timeout=2.0)


if __name__ == "__main__":
    try:
        exit_code = int(main())
    except Exception as e:
        log_path = _write_fatal_log(e)
        sys.stderr.write(f"DeepMatrix 启动失败：{e}\n日志：{log_path}\n")
        _show_fatal_dialog(f"DeepMatrix 启动失败：{e}\n\n日志：{log_path}")
        raise SystemExit(1)
    raise SystemExit(exit_code)
