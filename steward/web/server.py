"""HTTP plumbing: routes, JSON, SSE, static files. Standard library only.

Why `http.server` and not FastAPI: the project keeps its dependency list at one
package, and everything below the router is already tested through
`WebService`. A framework would buy routing helpers we do not need for six
endpoints, at the cost of the "read it end to end" property.

The server is threaded because SSE connections are long-lived: one thread parks
on the stream while the others answer requests. Each turn runs on its own thread
with its own event loop, which is the only place asyncio appears here.

Bind to localhost by default. This process can run shell commands; it is not a
multi-user service and has no authentication.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from steward.config import Config
from steward.web.service import Busy, UnknownSession, WebService

STATIC_DIR = Path(__file__).parent / "static"
INDEX = STATIC_DIR / "index.html"
POLL_INTERVAL = 0.12
PING_INTERVAL = 12.0
MAX_BODY = 2_000_000

MISSING_BUILD = (
    "前端还没有构建。\n\n"
    "在仓库根目录运行：\n"
    "    make web        # 或 cd web && npm install && npm run build\n\n"
    "然后刷新这个页面。\n"
)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}


class StewardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: WebService, quiet: bool = False) -> None:
        self.service = service
        self.quiet = quiet
        self.stopping = threading.Event()
        super().__init__(address, RequestHandler)

    def stop(self) -> None:
        self.stopping.set()


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: StewardServer

    # -- routing --------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._guard(self._get)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        self._guard(self._post)

    def _guard(self, handler) -> None:
        try:
            handler()
        except BrokenPipeError:
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - one bad request must not kill the server
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _get(self) -> None:
        url = urlparse(self.path)
        path = url.path

        if path == "/api/status":
            return self._json(200, self.server.service.status())
        if path == "/api/sessions":
            return self._json(200, {"sessions": self.server.service.sessions()})
        if path.startswith("/api/sessions/"):
            return self._transcript(path[len("/api/sessions/"):])
        if path == "/api/stream":
            return self._stream(parse_qs(url.query))
        if path.startswith("/api/"):
            return self._json(404, {"error": f"no route for GET {path}"})

        # Static files and the SPA fallback: the bundle is a single page, so an
        # unknown path is still index.html rather than a 404 page.
        if path in ("/", "/index.html"):
            return self._file(INDEX)
        target = _resolve_static(path)
        if target is not None and target.is_file():
            return self._file(target)
        return self._file(INDEX)


    def _post(self) -> None:
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/sessions":
            session_id = self.server.service.new_session()
            return self._json(200, {"session_id": session_id})
        if path == "/api/turn":
            text = str(body.get("text") or "")
            session_id = str(body.get("session") or "")
            try:
                session_id = self.server.service.start_turn(session_id, text)
            except Busy as exc:
                return self._json(409, {"error": str(exc)})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200, {"session_id": session_id})
        if path == "/api/approval":
            approval_id = str(body.get("id") or "")
            decision = str(body.get("decision") or "")
            try:
                resolved = self.server.service.resolve_approval(approval_id, decision)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200 if resolved else 404, {"resolved": resolved})
        if path == "/api/permission":
            try:
                mode = self.server.service.set_permission(str(body.get("mode") or ""))
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(200, {"permission": mode})
        return self._json(404, {"error": f"no route for POST {path}"})

    # -- handlers -------------------------------------------------------

    def _transcript(self, session_id: str) -> None:
        try:
            payload = self.server.service.transcript(session_id)
        except UnknownSession:
            return self._json(404, {"error": f"no such session: {session_id}"})
        return self._json(200, payload)

    def _stream(self, query: dict) -> None:
        service = self.server.service
        cursor = _int(_first(query, "cursor"))
        header = self.headers.get("Last-Event-ID")
        if header:
            cursor = _int(header, cursor)

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_ping = time.monotonic()
        while not self.server.stopping.is_set():
            batch = service.events.since(cursor)
            if batch.lost:
                # The client fell off the deque. Rather than replay events it may
                # already have (duplicates in the transcript), ask it to reload
                # from SQLite and continue from now. In-flight deltas for the
                # current step are lost; everything committed is not.
                self._send_event(None, {"type": "resync"})
                cursor = service.events.cursor
                batch = service.events.since(cursor)
            for item in batch.events:
                cursor = int(item["seq"])
                self._send_event(cursor, item)
            if not batch.events and time.monotonic() - last_ping > PING_INTERVAL:
                self.wfile.write(b": ping\n\n")
                last_ping = time.monotonic()
            self.wfile.flush()
            if not batch.events:
                time.sleep(POLL_INTERVAL)


    def _send_event(self, event_id: int | None, payload: dict) -> None:
        chunk = ""
        if event_id is not None:
            chunk += f"id: {event_id}\n"
        chunk += f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        self.wfile.write(chunk.encode("utf-8"))

    def _file(self, path: Path) -> None:
        if not path.is_file():
            if path == INDEX:
                return self._text(503, MISSING_BUILD)
            return self._json(404, {"error": f"missing static file: {path.name}"})
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _text(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = _int(self.headers.get("Content-Length"), 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError(f"request body too large: {length} bytes")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("request body is not valid JSON") from None
        return value if isinstance(value, dict) else {}

    def log_message(self, fmt: str, *args) -> None:
        if self.server.quiet:
            return
        if "/api/stream" in self.path:
            return  # one line per reconnect is noise enough
        super().log_message(fmt, *args)


def bind(
    config: Config,
    host: str = "127.0.0.1",
    port: int = 8765,
    attempts: int = 10,
    quiet: bool = False,
    provider=None,
):
    """Create a server, stepping past ports that are already taken."""
    service = WebService(config, provider=provider)
    last_error: OSError | None = None
    for offset in range(attempts):
        try:
            return StewardServer((host, port + offset), service, quiet=quiet), service
        except OSError as exc:
            last_error = exc
    service.close()
    raise last_error or OSError(f"could not bind {host}:{port}")


def url_for(server: StewardServer) -> str:
    host, port = server.server_address[0], server.server_address[1]
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{port}/"


def _first(query: dict, key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""


def _int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _resolve_static(path: str) -> Path | None:
    """Map a URL path to a file under the build output, refusing traversal."""
    relative = path.lstrip("/")
    if not relative or ".." in Path(relative).parts:
        return None
    root = STATIC_DIR.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


