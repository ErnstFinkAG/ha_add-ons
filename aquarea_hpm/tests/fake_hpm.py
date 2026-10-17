"""Tiny stand-in for the HPM web interface, modelled on markup posted by HPM owners.

Run: python3 fake_hpm.py 8099
"""

import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VALUES = {
    "37:6.4.7": ["SW-Spei", "48.0", "°C", None],
    "55:6.4.3": ["SW-NN", "45.0", "°C", ("number", 35, 60)],
    "60:1.1.1": ["Betriebsart", "Auto", "", ("select", ["Aus", "Auto", "Heizen", "Kühlen"])],
    "126:6.6.22": ["Freq", "0", "", None],
    "102:2.2.1.4": ["Status", "Ein", "", None],
    "17:6.2.1": ["RaumNZ1", "22.0", "°C", None],
    "33:6.1.1": ["Aussen", "-3.5", "°C", None],
    "201:9.1.1": ["Komfort Raum", "21.0", "°C", ("number", 15, 28)],
}
PAGES = {
    "v0.rsp": ("Anlagenübersicht", ["33:6.1.1"]),
    "v21.rsp": ("Wärmepumpe 1", ["126:6.6.22", "102:2.2.1.4"]),
    "v107000.rsp": ("Brauchwasser", ["37:6.4.7", "55:6.4.3", "60:1.1.1"]),
    "v50.rsp": ("Heizkreis 1", ["17:6.2.1"]),
    "menue.rsp?branchnr=1&level=1": ("Menü", []),
    "menue.rsp?branchnr=1&level=2": ("Raumtemperaturen", ["201:9.1.1"]),
}
SESSIONS = set()
UNLOCKED = set()
ACCESS_CODE = "5555"
WRITES = []


def nav(sid):
    links = "".join(f'<a class="navlink" href="{k}{"&" if "?" in k else "?"}sessionid={sid}">{t}</a> ' for k, (t, _) in PAGES.items() if k != "menue.rsp?branchnr=1&level=2")
    return links + f'<a class="navlink" href="leave.rsp?sessionid={sid}">Abmelden</a>'


def page(key, sid):
    title, ids = PAGES[key]
    body = []
    for dp in ids:
        label, value, unit, editor = VALUES[dp]
        text = f"{label}{' ' * (28 - len(label))}{value} {unit}".rstrip()
        link = f'<span class="dp"><a class="infolink" href="vinfo.rsp?sessionid={sid}&id={dp}">{text}</a></span>'
        if editor:
            pv = key.split("?")[0]
            link = (f'<form action="setgrvalue.rsp" method="POST">{link}<input type="hidden" name="sessionid" value="{sid}">'
                    f'<input type="hidden" name="id" value="{dp}"><input type="hidden" name="pv" value="{pv}">'
                    f'<input type="submit" value="&nbsp; "></form>')
        body.append(f"<tr><td>{link}</td></tr>")
    if key == "menue.rsp?branchnr=1&level=1":
        body.append(f'<tr><td><a href="menue.rsp?sessionid={sid}&branchnr=1&level=2">Raumtemperaturen</a></td></tr>')
    return (f'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"><html><head>'
            f'<meta http-equiv="content-type" content="text/html;charset=ISO-8859-1"><title>{title}</title></head>'
            f'<body><div class="nav">{nav(sid)}</div><h1>{title}</h1><table>{"".join(body)}</table></body></html>')


def editor(dp, sid, pv):
    label, value, unit, (kind, *spec) = VALUES[dp]
    if kind == "select":
        opts = "".join(f'<option value="{i}"{" selected" if o == value else ""}>{o}</option>' for i, o in enumerate(spec[0]))
        field = f'<select name="val">{opts}</select>'
    else:
        field = f'<input type="text" name="val" value="{value}" size="6"> {unit} &nbsp; Min: {spec[0]}.0 &nbsp; Max: {spec[1]}.0'
    return (f'<html><head><title>{label}</title></head><body><form action="setgrvalue.rsp" method="POST">'
            f'<input type="hidden" name="sessionid" value="{sid}"><input type="hidden" name="id" value="{dp}">'
            f'<input type="hidden" name="pv" value="{pv}">{label}: {field}'
            f'<input type="submit" name="ok" value="OK"><input type="submit" name="cancel" value="Abbrechen"></form>'
            f'<a href="{pv}?sessionid={sid}">zurück</a></body></html>')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, html, code=200):
        data = html.encode("iso-8859-1")
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            sid = "%08X" % random.getrandbits(32)
            SESSIONS.add(sid)
            self.send_response(302)
            self.send_header("Location", f"/v0.rsp?sessionid={sid}")
            self.end_headers()
            return
        sid = q.pop("sessionid", None)
        if url.path == "/leave.rsp":
            SESSIONS.discard(sid)
            return self.send("<html><body>Abgemeldet</body></html>")
        if sid not in SESSIONS:
            return self.send("<html><body>Sitzung abgelaufen</body></html>")
        key = url.path.lstrip("/") + ("?" + "&".join(f"{k}={v}" for k, v in q.items()) if q else "")
        if key not in PAGES:
            return self.send("not found", 404)
        if key == "menue.rsp?branchnr=1&level=2" and sid not in UNLOCKED:
            return self.send(f'<html><body><form action="code.rsp" method="POST">Code: <input type="password" name="code">'
                             f'<input type="hidden" name="sessionid" value="{sid}"><input type="submit" value="OK"></form></body></html>')
        self.send(page(key, sid))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("iso-8859-1")).items()}
        sid = form.get("sessionid")
        if urlparse(self.path).path == "/code.rsp" and sid in SESSIONS:
            if form.get("code") == ACCESS_CODE:
                UNLOCKED.add(sid)
            return self.send(page("v0.rsp", sid))
        if sid not in SESSIONS or urlparse(self.path).path != "/setgrvalue.rsp":
            return self.send("<html><body>Sitzung abgelaufen</body></html>")
        dp, pv = form["id"], form["pv"]
        if "val" not in form:
            return self.send(editor(dp, sid, pv))
        assert "cancel" not in form, "cancel button must not be submitted"
        kind, *spec = VALUES[dp][3]
        VALUES[dp][1] = spec[0][int(form["val"])] if kind == "select" else form["val"]
        WRITES.append((dp, VALUES[dp][1]))
        self.send(page(pv, sid))


def serve(port):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8099).serve_forever()
