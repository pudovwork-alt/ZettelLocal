from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser

import uvicorn


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def choose_port() -> int:
    preferred = int(os.environ.get("ZETTEL_PORT", "8765"))
    for port in range(preferred, preferred + 20):
        if port_is_free(port):
            return port
    raise RuntimeError("Could not find a free local port")


def open_browser(url: str) -> None:
    time.sleep(1.2)
    webbrowser.open(url)


if __name__ == "__main__":
    port = choose_port()
    url = f"http://127.0.0.1:{port}"
    print("=" * 62)
    print(" ZettelLocal - local knowledge base")
    print(f" Open in your browser: {url}")
    print(" Press Ctrl+C to stop")
    print("=" * 62)
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)
