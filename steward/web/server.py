"""Run the FastAPI app with uvicorn.

Kept separate from `api.py` so importing the application (tests, an embedding
host) never pulls in a server, and so the CLI can print its banner before the
loop starts.

Bind to localhost by default: this process can run shell commands, it has no
authentication, and it is not a multi-user service.
"""

from __future__ import annotations

import socket
import webbrowser

from steward.config import Config

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_ATTEMPTS = 10


class WebUnavailable(RuntimeError):
    """The optional web dependencies are not installed."""


def find_port(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, attempts: int = PORT_ATTEMPTS) -> int:
    """First free port at or after `port`, so a second instance still starts."""
    for offset in range(attempts):
        candidate = port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise OSError(f"no free port in {port}..{port + attempts - 1} on {host}")


def url_for(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{shown}:{port}/"


def serve(
    config: Config,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    quiet: bool = False,
    open_browser: bool = True,
) -> None:
    """Block serving the UI. Ctrl-C is handled by uvicorn."""
    try:
        import uvicorn

        from steward.web.api import create_app
    except ImportError as exc:
        raise WebUnavailable(
            "the web UI needs FastAPI and uvicorn: pip install -e '.[web]'"
        ) from exc

    url = url_for(host, port)
    app = create_app(
        config,
        on_ready=(lambda: webbrowser.open(url)) if open_browser else None,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning" if quiet else "info",
        access_log=not quiet,
    )
