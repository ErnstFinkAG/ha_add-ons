"""Small page shown in Home Assistant (ingress) to look at the values and download the saved pages."""

from __future__ import annotations

import collections
import html
import io
import logging
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LOGGER = logging.getLogger(__name__)
# Supervisor's ingress proxy; 127.0.0.1 for local tests.
ALLOWED_CLIENTS = {"172.30.32.2", "127.0.0.1"}


class LogBuffer(logging.Handler):
    """Keeps the last log lines so they can go into the download."""

    def __init__(self, size: int = 2000):
        super().__init__()
        self.lines = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        self.lines.append(self.format(record))


def start(bridge, log_buffer: LogBuffer, port: int = 8099):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.client_address[0] not in ALLOWED_CLIENTS:
                self.send_error(403)
                return
            path = self.path.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            if path == "aquarea_hpm_debug.zip":
                return self._send(self._zip(), "application/zip",
                                  {"Content-Disposition": 'attachment; filename="aquarea_hpm_debug.zip"'})
            return self._send(self._page().encode("utf-8"), "text/html; charset=utf-8")

        def _zip(self) -> bytes:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("log.txt", "\n".join(log_buffer.lines))
                rows = [f"{dp.id}\t{dp.page}\t{dp.page_title}\t{dp.label}\t{dp.raw}\t"
                        f"{'writable' if dp.writable else ''}\t{bridge.editors[dp.id].kind if dp.id in bridge.editors else ''}"
                        for dp in bridge.points.values()]
                archive.writestr("values.tsv", "id\tpage\tpage_title\tlabel\traw\twritable\teditor\n" + "\n".join(rows))
                if bridge.dump_dir is not None and bridge.dump_dir.exists():
                    for file in sorted(bridge.dump_dir.glob("*.html")):
                        archive.write(file, f"pages/{file.name}")
            return buffer.getvalue()

        def _page(self) -> str:
            rows = "".join(
                f"<tr><td>{html.escape(dp.page_title)}</td><td>{html.escape(dp.label)}</td>"
                f"<td>{html.escape(str(dp.value))} {html.escape(dp.unit or '')}</td>"
                f"<td>{html.escape(bridge.editors[dp.id].kind) if dp.id in bridge.editors else ''}</td>"
                f"<td><code>{html.escape(dp.id)}</code></td></tr>"
                for dp in bridge.points.values())
            pages = len(list(bridge.dump_dir.glob('*.html'))) if bridge.dump_dir is not None and bridge.dump_dir.exists() else 0
            note = (f"{pages} saved HPM pages are included." if bridge.dump_dir is not None
                    else "Turn on <b>dump_html</b> in the add-on options and restart to include the raw HPM pages.")
            return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aquarea HPM Bridge</title><style>
body{{font-family:system-ui,sans-serif;margin:16px;color:#1c1c1c;background:#fafafa}}
a.btn{{display:inline-block;padding:10px 16px;background:#03a9f4;color:#fff;border-radius:6px;text-decoration:none}}
table{{border-collapse:collapse;width:100%;margin-top:16px;font-size:14px}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#e1e1e1}}td,th{{border-color:#333}}}}
</style></head><body><h2>Aquarea HPM Bridge</h2>
<p>{len(bridge.points)} values, {len(bridge.editors)} controllable. {note}</p>
<p><a class="btn" href="aquarea_hpm_debug.zip">Download debug zip</a> (log, value list, saved pages)</p>
<table><tr><th>Page</th><th>Name</th><th>Value</th><th>Control</th><th>HPM id</th></tr>{rows}</table></body></html>"""

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    _LOGGER.info("Status page listening on port %d", port)
    return server
