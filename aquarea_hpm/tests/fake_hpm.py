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
    "33:6.1.1": ["", "-3.5", "°C", None],
    "34:6.1.2": ["", "Ein", "", None],
    "7:6.2.6": ["", "keine Störung", "", None],
}
PAGES = {
    "v0.rsp": ("Anlagenübersicht", ["33:6.1.1", "7:6.2.6", "34:6.1.2"]),
    "v21.rsp": ("Wärmepumpe 1", ["126:6.6.22", "102:2.2.1.4"]),
    "v107000.rsp": ("Brauchwasser", ["37:6.4.7", "55:6.4.3", "60:1.1.1"]),
    "v50.rsp": ("Heizkreis 1", ["17:6.2.1"]),
}
# WEB-FB menu tree: branchnr -> (name, children or value ids). Navigation is stateful like on
# the real HPM: menue.rsp?branchnr=B&level=L opens child B of the current menu (L = depth + 1)
# or goes up to the ancestor at depth L; anything else just shows the current menu again.
MENU = {
    1: ("Globales", {1: ("Meldungen", {1: ("Störstatus", {1: ("Status", [["Heizkr.1", "0"], ["Wärmep.1", "0"]])})}),
                     3: ("Service", {1: ("Hardware", [["Reglertemp", "31 °C"], ["ProdNr", "011511100047"]]),
                                     2: ("Raumtemperaturen", [["Komfort", "21.0 °C"]], 3),
                                     5: ("Zugriff", [], 7)}, 4),
                     5: ("Strukturierung", {2: ("WEB", [["Niveau 1", "11111111"]], 7)})}),
    2: ("MSR-GLT", {1: ("Heizkreise", {1: ("Heizkreis 1", {
        1: ("Istwerte", [["Vorlauf HK", "34.5 °C"], ["Jahreszeit: Sommer", None], ["Niveau 2", "22222222"], ["FText", ""]]),
        2: ("Sollwerte", [["RaumNZ1", "20.0 °C", (10, 30)], ["HK-Steilh", "0.6", (0.2, 3.5)], ["Xp", "25.0 K", (1, 99)]]),
        6: ("Service", {24: ("Fühlerkorrektur", [["Aussen", "0.0 K", (-5, 5)]])}),
    })})}),
    3: ("Zugriffscodes", []),
    5: ("Anlagenschemen", []),
}
NEVER_OPEN = {"Zugriffscodes", "Zugriff", "Anlagenschemen", "WEB"}
LOCKED = {"Raumtemperaturen"}
MENU_POS = {}
VIOLATIONS = []  # menus that were opened although they must not be
NAMES = {"33:6.1.1": "Aussentemperatur", "7:6.2.6": "Störmeldung HK1", "55:6.4.3": "Sollwert Nacht"}
MODE = {"value": 1}
MODES = ["Aus", "Auto", "Sommer", "Ferien", "Dauer", "Hand"]
SESSIONS = set()
UNLOCKED = set()
ACCESS_CODE = "5555"
WRITES = []


def nav(sid):
    links = "".join(f'<a class="navlink" href="{k}?sessionid={sid}">{t}</a> ' for k, (t, _) in PAGES.items())
    return links + f'<a class="navlink" href="leave.rsp?sessionid={sid}">Abmelden</a>'


def page(key, sid):
    title, ids = PAGES[key]
    return wrap(title, rows(key, ids, sid), sid)


def wrap(title, body, sid):
    return (f'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"><html><head>'
            f'<meta http-equiv="content-type" content="text/html;charset=ISO-8859-1"><title>{title}</title></head>'
            f'<body><div class="nav">{nav(sid)}</div><h1>{title}</h1><table>{"".join(body)}</table></body></html>')


def menu_node(pos):
    names, numbers, node = [], [], ("Hauptmenü", MENU)
    for b in pos:
        node = node[1][b]
        names.append(node[0])
        numbers.append(node[2] if len(node) > 2 else b)  # the HPM's menu number can differ from branchnr
    return names, numbers, node


def menu(sid, branchnr, level):
    pos = MENU_POS.get(sid, ())
    children = menu_node(pos)[2][1]
    if level == len(pos) + 1 and isinstance(children, dict) and branchnr in children:
        pos = pos + (branchnr,)
    elif level <= len(pos) and (level == 0 or pos[level - 1] == branchnr):
        pos = pos[:level]
    names, numbers, (name, children, *_) = menu_node(pos)
    if name in NEVER_OPEN:
        VIOLATIONS.append(name)
    if name in LOCKED and sid not in UNLOCKED:
        return None
    MENU_POS[sid] = pos
    head = ""
    if pos:
        up = (pos[-2], len(pos) - 1) if len(pos) > 1 else (1, 0)
        head = f'<tr><th><a href="menue.rsp?sessionid={sid}&branchnr={up[0]}&level={up[1]}">{"    ".join(n[:8] for n in names[-2:])}</a></th><th></th></tr>'
    if isinstance(children, dict):
        body = "".join(f'<tr><td><a href="menue.rsp?sessionid={sid}&branchnr={b}&level={len(pos) + 1}">{n}</a></td></tr>'
                       for b, (n, *_) in children.items())
    else:  # parameter rows, padded like on the real HPM
        body = ""
        for i, (label, value, *rng) in enumerate(children, 1):
            text = label if value is None else f"{label}{' ' * max(2, 34 - len(label) - len(value))}{value}"
            body += f'<tr><td><a href="info.rsp?sessionid={sid}&branchnr={i}&level={len(pos)}" id="{i}">{text}</a></td>'
            if rng:
                body += f'<td><a href="change.rsp?sessionid={sid}&branchnr={i}&level={len(pos)}"><img src="modify.png" alt="ändern"></a></td>'
            body += "</tr>"
    number = f"<p>Menü {'.'.join(map(str, numbers))}.x</p>" if pos else ""
    return wrap("WEB-FB", [f'<div id="mainpane">{number}<table class="mentext">{head}{body}</table></div>'], sid)


def menu_editor(sid, branchnr, level):
    """Editor behind the pencil icon of a WEB-FB menu row (same layout as the overview editor)."""
    pos = MENU_POS.get(sid, ())
    assert level == len(pos), (level, pos)
    label, value, (lo, hi) = menu_node(pos)[2][1][branchnr - 1]
    return (f'<html><body><h3>Kurzbezeichnung: {label}</h3><h4>{label} lang</h4>'
            f'<form method="POST" action="setmenu.rsp"><input type="hidden" name="sessionid" value="{sid}">'
            f'<input type="hidden" name="branchnr" value="{branchnr}"><input type="hidden" name="level" value="{level}">'
            f'<table class="change"><tr><td>Neuer Wert</td><td><input name="val" type="text"></td></tr>'
            f'<tr><td>Bisheriger Wert</td><td> {value.split()[0]}</td></tr><tr><td>Untergrenze</td><td> {lo}</td></tr>'
            f'<tr><td>Obergrenze</td><td> {hi}</td></tr></table>'
            f'<input type="submit" name="Set" value="OK"><input type="submit" name="Abort" value="Abbrechen"></form></body></html>')


def rows(key, ids, sid):
    body = []
    for dp in ids:
        label, value, unit, editor = VALUES[dp]
        text = (f"{label}{' ' * (28 - len(label))}{value} {unit}" if label else f"     {value} {unit}").rstrip()
        link = f'<span class="dp"><a class="infolink" href="vinfo.rsp?sessionid={sid}&id={dp}">{text}</a></span>'
        if editor:
            pv = key.split("?")[0]
            link = (f'<form action="setgrvalue.rsp" method="POST">{link}<input type="hidden" name="sessionid" value="{sid}">'
                    f'<input type="hidden" name="id" value="{dp}"><input type="hidden" name="pv" value="{pv}">'
                    f'<input type="submit" value="&nbsp; "></form>')
        body.append(f"<tr><td>{link}</td></tr>")
    # Hidden "set" button of a value that is currently unavailable (real HPM markup)
    body.append(f'<span class="set" style="display: none;"><form action="setgrvalue.rsp" method="POST"><input type="hidden" name="sessionid" value="{sid}">'
                f'<input type="hidden" name="id" value="19:6.2.4"><input type="hidden" name="pv" value="{key}"></form></span>')
    if key == "v21.rsp":
        body.append(f'<span class="dp"><a class="infolink" href="vinfo.rsp?sessionid={sid}&id=102:2.2.1.9">Status</a></span>')
    if key == "v0.rsp":
        opts = "".join(f'<option value="{i}"{" selected" if i == MODE["value"] else ""}>{m}</option>' for i, m in enumerate(MODES))
        body.append(f'<div>BArtSchalter<br><form id="MS0Form" method="POST" action="ms.rsp"><select name="MS0">{opts}</select>'
                    f'<input type="submit" value="OK"><input type="hidden" name="sessionid" value="{sid}"></form></div>')
    return body


def editor(dp, sid, pv):
    """Same structure as the real HPM editor (execgrset.rsp, German table)."""
    label, value, unit, (kind, *spec) = VALUES[dp]
    if kind == "select":
        opts = "".join(f'<option value="{i}"{" selected" if o == value else ""}>{o}</option>' for i, o in enumerate(spec[0]))
        field, rows = f'<select name="val">{opts}</select>', ""
    else:
        field = '<input name="val" type="text" size="20" maxlength="42">'
        rows = (f"<tr><td>Bisheriger Wert</td><td> {value}</td></tr><tr><td>Untergrenze</td><td> {spec[0]}.0</td></tr>"
                f"<tr><td>Obergrenze</td><td> {spec[1]}.0</td></tr>")
    return (f'<html><head><title>{label}</title></head><body><a href="v0.rsp?sessionid={sid}">Startbild</a><center>'
            f'<h2>Parameter {dp.split(":")[1]}</h2><h3>Kurzbezeichnung: {label}      </h3><h4>{NAMES.get(dp, label)}</h4>'
            f'<form method="POST" action="execgrset.rsp" name="valueform"><input type="hidden" name="sessionid" value="{sid}">'
            f'<table class="change"><tr><td>Neuer Wert</td><td>{field}</td></tr>{rows}</table>'
            f'<input type="submit" name="Set" value="OK"><input type="submit" name="Abort" value="Abbrechen">'
            f'<input type="hidden" name="id" value="{dp}"><input type="hidden" name="pv" value="{pv}"></form></center></body></html>')


def info(dp, sid):
    """Same structure as the real HPM vinfo.rsp page."""
    rows = f"<tr><td>Parameternummer:</td><td>{dp.split(':')[1]}</td></tr>"
    if dp in VALUES:
        label, value, unit, _ = VALUES[dp]
        short = label or "Kurz"
        rows += f'<tr><td class="icap">Aktueller Wert:</td><td class="icont">{short}{" " * 20}{value} {unit}</td></tr>'
        if dp in NAMES:
            rows += f'<tr><td class="icap">Langtext:</td><td class="icont">{NAMES[dp]}</td></tr>'
    return (f'<html><body><a href="v0.rsp?sessionid={sid}">Startbild</a><center><table class="info">'
            f'<tr><th colspan="2">Parameterinformationen</th></tr>{rows}</table></center></body></html>')


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
        if url.path == "/webfb.rsp":
            return self.send(f'<html><body><a href="v0.rsp?sessionid={sid}">Startbild</a>'
                             f'<a href="menue.rsp?sessionid={sid}&level=0&branchnr=1">Hauptmenü</a>'
                             f'<form action="code.rsp"><input type="hidden" name="sessionid" value="{sid}">'
                             f'<input type="submit" value="Zugriffscode eingeben"></form></body></html>')
        if url.path == "/code.rsp":
            return self.send(f'<html><body><form action="setcode.rsp" method="POST"><input type="hidden" name="sessionid" value="{sid}">'
                             f'Code: <input name="code" type="text"><input type="submit" value="OK"></form></body></html>')
        if url.path == "/change.rsp":
            return self.send(menu_editor(sid, int(q["branchnr"]), int(q["level"])))
        if url.path == "/vinfo.rsp":
            return self.send(info(q["id"], sid))
        if url.path == "/menue.rsp":
            html = menu(sid, int(q.get("branchnr", 0)), int(q.get("level", 99)))  # bare: current menu
            if html:
                return self.send(html)
        elif key not in PAGES:
            return self.send("not found", 404)
        if url.path == "/menue.rsp":
            return self.send(f'<html><body><form action="code.rsp" method="POST">Code: <input type="password" name="code">'
                             f'<input type="hidden" name="sessionid" value="{sid}"><input type="submit" value="OK"></form></body></html>')
        self.send(page(key, sid))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("iso-8859-1")).items()}
        sid = form.get("sessionid")
        if urlparse(self.path).path in ("/code.rsp", "/setcode.rsp") and sid in SESSIONS:
            if form.get("code") == ACCESS_CODE:
                UNLOCKED.add(sid)
            return self.send(page("v0.rsp", sid))
        if sid not in SESSIONS:
            return self.send("<html><body>Sitzung abgelaufen</body></html>")
        if urlparse(self.path).path == "/setmenu.rsp":
            assert "Abort" not in form, form
            row = menu_node(MENU_POS[sid])[2][1][int(form["branchnr"]) - 1]
            unit = row[1].split()[1:]
            row[1] = " ".join([form["val"]] + unit)
            WRITES.append((row[0], form["val"]))
            return self.send(menu(sid, 0, 99))
        if urlparse(self.path).path == "/ms.rsp":
            MODE["value"] = int(form["MS0"])
            WRITES.append(("ms:MS0", MODES[MODE["value"]]))
            return self.send(page("v0.rsp", sid))
        dp, pv = form["id"], form["pv"]
        if urlparse(self.path).path == "/setgrvalue.rsp":
            if dp == "19:6.2.4":
                return self.send(f'<html><body>Dieser Parameter ist zur Zeit nicht verfügbar<br><a href="{pv}?sessionid={sid}">zurück</a></body></html>')
            return self.send(editor(dp, sid, pv))
        assert urlparse(self.path).path == "/execgrset.rsp" and "Abort" not in form, form
        kind, *spec = VALUES[dp][3]
        VALUES[dp][1] = spec[0][int(form["val"])] if kind == "select" else form["val"]
        WRITES.append((dp, VALUES[dp][1]))
        self.send(page(pv, sid) if pv in PAGES else menu(sid, 0, 99))


def serve(port):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8099).serve_forever()
