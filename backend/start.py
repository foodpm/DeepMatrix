import os
import socket
import threading
import time
import webbrowser
import sys
import uvicorn
import traceback
import asyncio

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Fix for Windows asyncio loop issue
if sys.platform == 'win32':
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


def _show_error_dialog(title: str, message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        pass


def _write_startup_error(text: str) -> str:
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        log_dir = os.path.join(base, "GoodsRecognitionModel")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "startup_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
        return log_path
    except Exception:
        pass


def _get_local_ipv4s() -> list[str]:
    ips: set[str] = set()
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            ips.add(ip)
    except Exception:
        pass

    for target in ("8.8.8.8", "1.1.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 80))
            ips.add(s.getsockname()[0])
            s.close()
        except Exception:
            pass

    def _is_publicly_usable_ipv4(ip: str) -> bool:
        if ip == "0.0.0.0":
            return False
        if ip.startswith("127."):
            return False
        if ip.startswith("169.254."):
            return False
        return True

    return sorted(ip for ip in ips if _is_publicly_usable_ipv4(ip))


def main():
    # Fix for Windows asyncio loop issue (WinError 10038)
    if sys.platform == 'win32':
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")

        host = "0.0.0.0"
        preferred_port = int(os.environ.get("SHELF_PORT") or 8000)
        port = _pick_port(host, preferred_port)
        if port <= 0:
            raise RuntimeError("no available port")

        url = f"http://127.0.0.1:{port}/"
        urls = [url] + [f"http://{ip}:{port}/" for ip in _get_local_ipv4s()]
        print("服务已启动，可用访问地址：")
        for u in urls:
            print(f"- {u}")

        def _open():
            time.sleep(0.8)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        disable_browser = str(os.environ.get("SHELF_NO_BROWSER") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
        if not disable_browser:
            threading.Thread(target=_open, daemon=True).start()
        uvicorn.run(app, host=host, port=port, log_level="info")
    except Exception:
        err_text = traceback.format_exc()
        log_path = _write_startup_error(err_text)
        msg = "程序启动失败。"
        if log_path:
            msg += f"\n\n错误日志：{log_path}"
        _show_error_dialog("商品识别模型", msg)


if __name__ == "__main__":
    main()
