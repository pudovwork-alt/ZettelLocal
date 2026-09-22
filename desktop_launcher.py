from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path


def ensure_gui_streams() -> None:
    """PyInstaller --windowed sets stdout/stderr to None on Windows.

    Some libraries (notably Uvicorn logging formatters) expect file-like streams
    and call isatty(). Give them harmless UTF-8 sinks in GUI mode.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", buffering=1)


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    target = base / "ZettelLocal" / "data"
    target.mkdir(parents=True, exist_ok=True)
    return target


def prepare_data() -> Path:
    target = user_data_dir()
    seed = runtime_root() / "data" / "zettel.db"
    db = target / "zettel.db"
    if not db.exists() and seed.exists():
        shutil.copy2(seed, db)
    (target / "model").mkdir(exist_ok=True)
    os.environ["ZETTEL_DATA_DIR"] = str(target)
    return target


def choose_port(start: int = 8765) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Не удалось найти свободный локальный порт")


def wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.08)


def main() -> None:
    ensure_gui_streams()
    prepare_data()
    port = choose_port()

    import uvicorn
    from app.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,  # GUI EXE has no console; avoid Uvicorn isatty() formatter crash.
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="ZettelLocalServer", daemon=True)
    thread.start()
    wait_for_port(port)

    import webview

    window = webview.create_window(
        "ZettelLocal",
        f"http://127.0.0.1:{port}",
        width=1440,
        height=920,
        min_size=(1050, 680),
        resizable=True,
        text_select=True,
    )

    def on_closed() -> None:
        server.should_exit = True

    window.events.closed += on_closed
    webview.start(debug=False, private_mode=False)


if __name__ == "__main__":
    main()
