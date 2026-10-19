"""Client for the web interface of the Panasonic Aquarea Heat Pump Manager (PAW-HPM / HPM1).

The HPM serves plain HTML 4 pages (ISO-8859-1) named ``*.rsp``. Every URL carries a
``sessionid`` query parameter. Values are shown as links of the form::

    <span class="dp"><a class="infolink" href="vinfo.rsp?sessionid=4198B670&id=37:6.4.7">SW-Spei   48.0 °C</a></span>

Values that can be changed are wrapped in (or accompanied by) a form posting to
``setgrvalue.rsp`` with hidden ``sessionid``, ``id`` and ``pv`` (return page) fields.
Submitting that form opens an editor page; submitting the editor's form stores the value.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qsl, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

SESSION_RE = re.compile(r"sessionid=([0-9A-Za-z]+)")
NUMBER_RE = re.compile(r"^(?:(?P<label>.*?)\s+)?(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*(?P<unit>[°%A-Za-zµ/][^\s]{0,7})?$")
# Pages worth crawling. Everything else (leave.rsp, resize.rsp, webfb.rsp, vinfo.rsp,
# setgrvalue.rsp ...) either logs out, changes the layout or only shows detail.
CRAWL_RE = re.compile(r"^/?(v\d+\.rsp)$", re.I)
# WEB-FB menu nodes never opened: access codes, plant schema selection and action-like entries.
MENU_SKIP_RE = re.compile(r"zugriff|passw|kennw|anlagenschem|werkseinst|reset|l(ö|oe)sch|quittier|neustart|test|^web$|^trend$|projektmanag", re.I)
# Menu levels that say nothing about what a value is; left out of entity names.
GENERIC_MENUS = {"msr-glt", "globales", "funktion", "service", "wärmeerzeuger", "heizkreise", "uhren",
                 "uhr kanalauswahl", "uhr status", "uhr service", "konfiguration", "schnittstellen"}


# WEB-FB menu values worth having in Home Assistant: (menu path regex, row label regex, settable).
# "settable" rows become number/select/text entities; everything else here is a read-only sensor.
_USEFUL = [
    # what the system is doing right now
    (r"/ (Istwerte|Status)$", r".", False),
    (r"(Wärmepumpe \d+|Pufferspeicher|Photovoltaik|Smart Grid) / Sollwerte$", r".", False),
    (r"/ Uhr Status / ", r".", False),
    (r"^Globales / (Meldungen / Störstatus|Fehler / System) / (Status|Texte)$", r".", False),
    (r"^Globales / Service / (Hardware|Software)$", r"^(?!Sprache)", False),
    # settings worth programming from Home Assistant
    (r"Heizkreis \d+ / Sollwerte$", r"^(RaumNZ\d|RaumNN|RaumSNNZ|HK-Steilh|Hk-Exp)$", True),
    (r"Heizkreis \d+ / Funktion / Sommerabschaltung$", r"^(Aktiv|AusTemp|EinTemp)", True),
    (r"Heizkreis \d+ / Funktion / Sollwertbegrenzung$", r"^(minVL|maxVL)$", True),
    (r"Heizkreis \d+ / Funktion / Frostgr", r"^AussenFrGr$", True),
    (r"Heizkreis \d+ / Funktion / Ausl", r"^(AuslAT|GrAussen)$", True),
    (r"Heizkreis \d+ / Funktion / Fernbed", r"^DauÜberstd$", True),
    (r"Pufferspeicher / Sollwerte$", r"^ÜberhZ\d$", True),
    (r"Pufferspeicher / Funktion / (Überhöhung|Pufferspeicher)$", r"^(Aktiv|Überh\.HK\d|maxTSpei|minAus)$", True),
    (r"Wärmepumpe \d+ / Funktion / (Sollwertbegrenzung|Überhöhung|Bivalenz)$", r"^(maxSW|Aktiv|Überh\.HK|Grenze)$", True),
    (r"/ Uhr Kanalauswahl / .+ / (Wochenprogramm|Sonder-Nichtn\.|Sonder-Nutzungsz\.)$", r".", True),
    (r"^Globales / Systemuhr / (Uhrzeit|Datum|Mode)$", r"^(AktZeit|AktDatum|ArtSoWiUmsch)$", True),
    (r"Photovoltaik / Funktion / (Temperaturen|Ablaufzeiten)$", r".", True),
    (r"Smart Grid / Funktion / Betriebszustand \d$", r".", True),
]
_USEFUL = [(re.compile(t), re.compile(l), w) for t, l, w in _USEFUL]


def menu_usefulness(title: str, label: str) -> Optional[str]:
    """"set" for recommended settings, "read" for useful read-only values, None for the rest."""
    found = None
    for title_re, label_re, settable in _USEFUL:
        if title_re.search(title) and label_re.search(label):
            if settable:
                return "set"
            found = "read"
    return found


def menu_title(title: str) -> str:
    """Short, distinctive name for values of a menu, e.g. "Wärmepumpe 1 Überhöhung"."""
    names = [n for n in title.split(" / ") if n and n.lower() not in GENERIC_MENUS]
    return " ".join(names[-2:]) or (title.split(" / ")[-1] if title else "Menü")
# Menu rows that hold user names or access codes; never read into Home Assistant.
SECRET_ROW_RE = re.compile(r"niveau|benutzer|passw|kennw|code|zugriff|pin\b", re.I)
MENU_NUMBER_RE = re.compile(r"Men(ü|ue)\s+((?:\d+\.)+)x", re.I)
RSP_REF_RE = re.compile(r"""([A-Za-z0-9_]+\.rsp(?:\?[^"'\s<>]*)?)""")
# Menu branches never read: they show or change access codes.
SKIP_TEXT_RE = re.compile(r"zugriff|passw|kennw|access", re.I)
# The WEB-FB button that asks for an access code to unlock more parameters.
CODE_ENTRY_RE = re.compile(r"zugriffscode\s+eingeben|code\s+eingeben|enter\s+code", re.I)
NEVER_CRAWL_RE = re.compile(r"(leave|logout|abmelden|resize|webfb|setgrvalue|reset|delete)", re.I)


class HpmError(Exception):
    pass


class HpmLocked(HpmError):
    """A page asks for an access code that is not configured or was refused."""


class HpmBusy(HpmError):
    """The HPM answered with sorry.rsp: all of its web sessions are in use."""


class MenuSessionLost(HpmError):
    """A WEB-FB menu page came back without a session (expired or logged out)."""


@dataclass
class Datapoint:
    id: str
    label: str
    raw: str
    value: object  # float, int or str
    unit: Optional[str]
    decimals: int
    page: str
    page_title: str
    writable: bool = False
    form: Optional[dict] = None  # hidden fields of the setgrvalue form
    inline_editor: Optional["Editor"] = None  # selects shown directly on the page (e.g. BArtSchalter)
    long_name: Optional[str] = None  # descriptive name from the HPM's info/editor page
    change: Optional[tuple] = None  # WEB-FB menu row with an edit link: (branchnr, level) of change.rsp
    menu_title: str = ""  # full WEB-FB menu path, e.g. "MSR-GLT / Heizkreise / Heizkreis 1 / Sollwerte"


@dataclass
class Page:
    key: str
    title: str
    datapoints: list = field(default_factory=list)
    links: list = field(default_factory=list)  # (key, text)
    code_entry: Optional[str] = None  # page key of the "Zugriffscode eingeben" button, if any


@dataclass
class Editor:
    """What the HPM editor page offers for one datapoint."""

    kind: str  # "number", "select" or "text"
    action: str
    method: str
    fields: dict
    value_field: str
    options: list = field(default_factory=list)  # [(value, text)]
    current: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    name: Optional[str] = None  # e.g. "Sollwert NZ1"


UNAVAILABLE_RE = re.compile(r"nicht verf(ü|ue)gbar|not available", re.I)


def page_key(url: str) -> str:
    """Path + query of a URL without the session id, e.g. ``menue.rsp?branchnr=1&level=4``."""
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != "sessionid"]
    if not query:
        return path
    return path + "?" + "&".join(f"{k}={v}" for k, v in query)


def _clean(text: str) -> str:
    return text.replace("\xa0", " ").replace("\r", " ").replace("\n", " ").strip()


def parse_value(text: str):
    """Split an HPM value text into (label, value, unit, decimals)."""
    text = _clean(text)
    match = NUMBER_RE.match(text)
    if match:
        raw = match.group("value").replace(",", ".")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        value = float(raw) if decimals else int(raw)
        return (match.group("label") or "").strip(), value, match.group("unit"), decimals
    parts = re.split(r"\s{2,}", text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), None, 0
    return text, text, None, 0


def _page_title(soup: BeautifulSoup, fallback: str) -> str:
    for selector in ("h1", "h2", ".headline", ".title", "title"):
        node = soup.select_one(selector)
        if node and _clean(node.get_text()):
            return _clean(node.get_text())
    return fallback


def _row_label(node) -> str:
    """Text of the table row/container around a value link, minus the link itself."""
    for parent in node.parents:
        if parent.name in ("tr", "li", "p", "div") and parent.get("class") != ["dp"]:
            text = _clean(parent.get_text(" "))
            own = _clean(node.get_text(" "))
            text = text.replace(own, "").strip()
            # The HPM positions values freely in one big container; its text is not a label.
            if text and len(text) <= 40 and len(parent.find_all("a")) <= 2:
                return re.sub(r"\s+", " ", text)
            break
    return ""


def parse_page(html: str, key: str, fallback_title: str = "") -> Page:
    soup = BeautifulSoup(html, "html.parser")
    page = Page(key=key, title=_page_title(soup, fallback_title or key))
    by_id: dict[str, Datapoint] = {}

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "vinfo.rsp" in href and "id=" in href:
            dp_id = dict(parse_qsl(urlparse(href).query)).get("id")
            if not dp_id or dp_id in by_id or not _clean(link.get_text(" ")):
                continue
            label, value, unit, decimals = parse_value(link.get_text(" "))
            if not label or label == str(value):
                label = _row_label(link) or f"Wert {dp_id}"
            by_id[dp_id] = Datapoint(dp_id, label, _clean(link.get_text(" ")), value, unit, decimals, key, page.title)
        else:
            target = urlparse(urljoin("http://hpm/", href))
            if CODE_ENTRY_RE.search(_clean(link.get_text(" "))) and not NEVER_CRAWL_RE.search(href):
                page.code_entry = page_key(href)
            elif CRAWL_RE.match(target.path) and not NEVER_CRAWL_RE.search(href):
                page.links.append((page_key(href), _clean(link.get_text(" "))))

    for form in soup.find_all("form"):
        action = form.get("action", "")
        hidden = {i.get("name"): i.get("value", "") for i in form.find_all("input") if i.get("name") and i.get("type", "").lower() == "hidden"}
        container = form.find_parent("span", class_="set")
        hidden_button = container is not None and "display: none" in container.get("style", "").replace("display:none", "display: none")
        select = form.find("select")
        if "setgrvalue" in action.lower() and "id" in hidden:
            if hidden_button:  # the HPM hides the button when the value is currently not available
                continue
            dp = by_id.get(hidden["id"])
            if dp is None:
                label, value, unit, decimals = parse_value(form.get_text(" "))
                if not label or label == str(value):
                    label = _row_label(form) or label
                dp = Datapoint(hidden["id"], label or f"Wert {hidden['id']}", _clean(form.get_text(" ")), value, unit, decimals, key, page.title)
                by_id[dp.id] = dp
            dp.writable = True
            dp.form = {"action": action, "method": form.get("method", "POST").upper(), "fields": hidden}
        elif select is not None and select.get("name") and form.get("method", "GET").upper() == "POST":
            # A selector shown directly on the page, e.g. the operating mode switch (ms.rsp, BArtSchalter).
            options = [(o.get("value", _clean(o.get_text())), _clean(o.get_text())) for o in select.find_all("option")]
            chosen = select.find("option", selected=True)
            current = _clean(chosen.get_text()) if chosen else ""
            label = ""
            for prev in form.previous_siblings:
                text = _clean(prev.get_text(" ") if hasattr(prev, "get_text") else str(prev))
                if text:
                    label = text
                    break
            dp_id = f"{urlparse(action).path.lstrip('/').split('.')[0]}:{select['name']}"
            editor = Editor("select", action=action, method="POST", fields=hidden, value_field=select["name"],
                            options=options, current=current, name=label or None)
            by_id[dp_id] = Datapoint(dp_id, label or select["name"], current, current, None, 0, key, page.title,
                                     writable=True, inline_editor=editor, long_name=label or None)
        else:
            target = urlparse(urljoin("http://hpm/", action))
            button_text = " ".join([_clean(form.get_text(" "))] + [i.get("value", "") for i in form.find_all("input")
                                                                   if i.get("type", "").lower() == "submit"])
            if CODE_ENTRY_RE.search(button_text) and form.get("method", "GET").upper() == "GET" and action:
                page.code_entry = page_key(action)
            elif form.get("method", "GET").upper() == "GET" and CRAWL_RE.match(target.path) and not NEVER_CRAWL_RE.search(action):
                page.links.append((page_key(action), _clean(form.get_text(" ")) or page_key(action)))

    # Navigation that is done with JavaScript (onclick, location.href, frames) instead of links
    known = {k for k, _ in page.links}
    for ref in RSP_REF_RE.findall(html.replace("&amp;", "&")):
        ref_key = page_key(ref)
        if ref_key == "menue.rsp":  # e.g. a hidden "pv" return-page field; a menu needs its branch
            continue
        if ref_key not in known and CRAWL_RE.match(urlparse(ref).path) and not NEVER_CRAWL_RE.search(ref):
            known.add(ref_key)
            page.links.append((ref_key, ref_key))

    page.datapoints = list(by_id.values())
    return page


def _num(text) -> Optional[float]:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "."))
    except ValueError:
        return None


def _heading_name(soup: BeautifulSoup) -> Optional[str]:
    """Descriptive name on HPM parameter pages: <h3>Kurzbezeichnung: RaumNZ1</h3><h4>Sollwert NZ1</h4>."""
    for tag in ("h4", "h3"):
        node = soup.find(tag)
        if node and _clean(node.get_text()):
            return re.sub(r"^\s*Kurzbezeichnung:\s*", "", _clean(node.get_text())).strip() or None
    return None


def parse_info(html: str) -> tuple[Optional[str], bool]:
    """Name of a value from its vinfo.rsp page and whether the page shows a value at all.

    The HPM's "Parameterinformationen" table has rows like ``Aktueller Wert: Aussen   13.5 °C``
    and ``Langtext: Aussentemperatur``. Links that only lead to a status page show nothing
    but the parameter number.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) >= 2:
            key = _clean(cells[0].get_text(" ")).rstrip(":").lower()
            rows[key] = cells[1].get_text(" ").replace("\xa0", " ").strip()
    if not rows:
        return _heading_name(soup), True
    current = rows.get("aktueller wert")
    name = _clean(rows.get("langtext", "")) or None
    if name is None and current:
        name = re.split(r"\s{2,}", current, maxsplit=1)[0].strip() or None
    return name, current is not None


def parse_editor(html: str) -> Optional[Editor]:
    """Parse the page the HPM shows after clicking a changeable value."""
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        select = form.find("select")
        inputs = [i for i in form.find_all("input") if i.get("name") and i.get("type", "text").lower() in ("text", "number", "")]
        if not select and not inputs:
            continue
        fields = {}
        for inp in form.find_all("input"):
            name, kind = inp.get("name"), inp.get("type", "text").lower()
            if not name:
                continue
            if kind == "hidden":
                fields[name] = inp.get("value", "")
            elif kind == "submit" and not any(w in (inp.get("value", "") + name).lower() for w in ("abbr", "cancel", "zur")):
                fields.setdefault(name, inp.get("value", ""))
        for button in form.find_all("button"):
            if button.get("name") and not any(w in _clean(button.get_text()).lower() for w in ("abbr", "cancel", "zur")):
                fields.setdefault(button["name"], button.get("value", ""))
        common = {"action": form.get("action", "setgrvalue.rsp"), "method": form.get("method", "POST").upper(), "fields": fields}
        if select is not None:
            options = [(o.get("value", _clean(o.get_text())), _clean(o.get_text())) for o in select.find_all("option")]
            chosen = select.find("option", selected=True)
            return Editor("select", value_field=select.get("name"), options=options, current=_clean(chosen.get_text()) if chosen else None,
                          name=_heading_name(soup), **common)
        inp = inputs[0]
        text = _clean(soup.get_text(" "))
        rows = {}
        for tr in soup.find_all("tr"):
            cells = [_clean(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) >= 2 and cells[0]:
                rows[cells[0].lower()] = cells[1]
        name = _heading_name(soup)
        minimum, maximum = _num(inp.get("min")), _num(inp.get("max"))
        minimum = minimum if minimum is not None else _num(rows.get("untergrenze") or rows.get("minimum"))
        maximum = maximum if maximum is not None else _num(rows.get("obergrenze") or rows.get("maximum"))
        if minimum is None:
            m = re.search(r"min(?:imum|\.)?\s*[:=]?\s*([-+]?\d+(?:[.,]\d+)?)", text, re.I)
            minimum = _num(m.group(1)) if m else None
        if maximum is None:
            m = re.search(r"max(?:imum|\.)?\s*[:=]?\s*([-+]?\d+(?:[.,]\d+)?)", text, re.I)
            maximum = _num(m.group(1)) if m else None
        if minimum is None or maximum is None:
            m = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*(?:\.\.\.?|…|bis)\s*([-+]?\d+(?:[.,]\d+)?)", text)
            if m:
                minimum, maximum = _num(m.group(1)), _num(m.group(2))
        current = inp.get("value") or rows.get("bisheriger wert") or rows.get("aktueller wert")
        kind = "number" if _num(current) is not None or minimum is not None else "text"
        return Editor(kind, value_field=inp.get("name"), current=current, minimum=minimum, maximum=maximum,
                      step=_num(inp.get("step")), name=name, **common)
    return None


def menu_number(html: str) -> tuple:
    """The HPM's own number of a WEB-FB menu ("Menü 1.3.4.x" -> (1, 3, 4)); () for the main menu."""
    match = MENU_NUMBER_RE.search(BeautifulSoup(html, "html.parser").get_text(" "))
    return tuple(int(n) for n in match.group(2).strip(".").split(".")) if match else ()


def _page_hint(html: str, length: int = 160) -> str:
    """The start of a page's visible text, to tell in the log what the HPM showed instead."""
    soup = BeautifulSoup(html, "html.parser")
    pane = soup.select_one("#mainpane") or soup.body or soup
    return _clean(pane.get_text(" "))[:length]


def _menu_row_value(text: str):
    """Split a menu row like 'Reglertemp     31 °C' into (label, value, unit, decimals, raw)."""
    text = text.replace("\xa0", " ").strip("\r\n")
    parts = re.split(r"\s{2,}", text.strip(), maxsplit=1)
    if len(parts) < 2 and ": " in text:
        parts = text.strip().split(": ", 1)
    label = parts[0].strip()
    raw = parts[1].strip() if len(parts) == 2 else ""
    if not raw or re.fullmatch(r"0\d+", raw):
        return label, raw, None, 0, raw
    _, value, unit, decimals = parse_value(raw)
    return label, value, unit, decimals, raw


def parse_menu_page(html: str, key: str, title: str) -> Page:
    """Values listed on a WEB-FB parameter menu (rows linking to info.rsp)."""
    soup = BeautifulSoup(html, "html.parser")
    page = Page(key=key, title=title)
    number = ".".join(map(str, menu_number(html)))
    for row in soup.select("#mainpane table.mentext tr"):
        links = row.select("td a[href]")
        if not links or urlparse(links[0]["href"]).path.lstrip("/") != "info.rsp":
            continue
        query = dict(parse_qsl(urlparse(links[0]["href"]).query))
        label, value, unit, decimals, raw = _menu_row_value(links[0].get_text())
        change = None
        if len(links) > 1 and urlparse(links[1]["href"]).path.lstrip("/") == "change.rsp":
            cq = dict(parse_qsl(urlparse(links[1]["href"]).query))
            if cq.get("branchnr", "").isdigit() and cq.get("level", "").isdigit():
                change = (int(cq["branchnr"]), int(cq["level"]))
        if not label or SECRET_ROW_RE.search(label) or not query.get("branchnr", "").isdigit():
            continue
        page.datapoints.append(Datapoint(
            id=f"menu:{number}.{query['branchnr']}" if number else f"menu:{key[5:]}.{query['branchnr']}",
            label=label, raw=raw, value=value, unit=unit, decimals=decimals, page=key,
            page_title=menu_title(title), writable=change is not None, change=change, menu_title=title))
    return page


class HpmClient:
    def __init__(self, host: str, username: str = "", password: str = "", timeout: float = 10.0, request_delay: float = 0.3,
                 session_file: Optional[str] = None, access_code: str = ""):
        self.base = host if host.startswith("http") else f"http://{host}"
        self.base = self.base.rstrip("/") + "/"
        self.username, self.password = username, password
        self.timeout, self.request_delay = timeout, request_delay
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "hpm2mqtt"
        self.sessionid: Optional[str] = None
        self.lock = threading.RLock()
        self._last_request = 0.0
        self.on_page = None  # optional callback(key, html) used while crawling
        self.session_file = session_file
        self.access_code = access_code
        self.code_entry_key: Optional[str] = None
        self._menu_pos: Optional[tuple] = None  # current WEB-FB menu position (None = unknown)
        self._menu_page: Optional[str] = None

    # -- low level ---------------------------------------------------------------------
    def _throttle(self):
        wait = self._last_request + self.request_delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _decode(self, resp: requests.Response) -> str:
        if resp.encoding is None or resp.encoding.lower() in ("utf-8", "ascii") and b"8859" in resp.content[:600]:
            resp.encoding = "iso-8859-1"
        return resp.text

    def _remember_session(self, url: str, html: str):
        ids = SESSION_RE.findall(url) + SESSION_RE.findall(html)
        if ids:
            sessionid = Counter(ids).most_common(1)[0][0]
            if sessionid != self.sessionid and self.session_file:
                try:
                    with open(self.session_file, "w", encoding="utf-8") as handle:
                        handle.write(sessionid)
                except OSError:
                    pass
            self.sessionid = sessionid

    def _stored_session(self) -> Optional[str]:
        try:
            with open(self.session_file or "", encoding="utf-8") as handle:
                return handle.read().strip() or None
        except OSError:
            return None

    def _request(self, method: str, path: str, data: Optional[dict] = None) -> tuple[str, str]:
        self._throttle()
        url = urljoin(self.base, path)
        resp = self.http.request(method, url, data=data, timeout=self.timeout, allow_redirects=True)
        resp.raise_for_status()
        html = self._decode(resp)
        self._remember_session(resp.url, html)
        return resp.url, html

    def _with_session(self, key: str) -> str:
        if self.sessionid is None:
            return key
        sep = "&" if "?" in key else "?"
        return f"{key}{sep}sessionid={self.sessionid}"

    @staticmethod
    def _needs_login(html: str) -> bool:
        return bool(re.search(r"<input[^>]+type=[\"']?password", html, re.I))

    # -- session -----------------------------------------------------------------------
    def logout(self):
        """Free the HPM web session (it only has a few) when the add-on stops."""
        with self.lock:
            if self.sessionid is None:
                return
            try:
                self._request("GET", f"leave.rsp?sessionid={self.sessionid}")
                _LOGGER.info("Logged out of the HPM")
            except requests.RequestException as err:
                _LOGGER.debug("Logout failed: %s", err)
            self.sessionid = None
            if self.session_file:
                try:
                    os.remove(self.session_file)
                except OSError:
                    pass

    def _submit_password(self, url: str, html: str, secret: str, username: str = "") -> tuple[str, str]:
        """Fill in and submit the form that has a password field (login or access code)."""
        soup = BeautifulSoup(html, "html.parser")
        form = next((f for f in soup.find_all("form") if f.find("input", attrs={"type": re.compile("password", re.I)})), None)
        text_only = False
        if form is None:  # code entry pages may use a plain text field
            form = next(f for f in soup.find_all("form")
                        if f.find("input", attrs={"type": re.compile("^(text|number)$", re.I)}) or
                        f.find("input", attrs={"type": False, "name": True}))
            text_only = True
        data = {}
        for inp in form.find_all("input"):
            name, kind = inp.get("name"), inp.get("type", "text").lower()
            if not name:
                continue
            if kind == "password" or text_only and kind in ("text", "number", ""):
                data[name] = secret
                text_only = False  # only the first text field gets the code
            elif kind in ("text", ""):
                data[name] = username
            elif kind == "hidden" or kind == "submit" and name not in data:
                data[name] = inp.get("value", "")
        if "sessionid" in data and self.sessionid:
            data["sessionid"] = self.sessionid
        return self._request(form.get("method", "POST").upper(), urljoin(url, form.get("action", "")), data)

    @staticmethod
    def _is_login_page(url: str) -> bool:
        return bool(re.search(r"/(login|enter|getlogin)\.rsp", url, re.I))

    def login(self) -> tuple[str, str]:
        with self.lock:
            self._menu_pos = None
            stored = self._stored_session() if self.sessionid is None else None
            if stored:
                # The add-on was restarted without logging out: take the old session back
                # instead of using up another of the HPM's few session slots.
                self.sessionid = stored
                try:
                    url, html = self._request("GET", f"v0.rsp?sessionid={stored}")
                    if not self._needs_login(html) and "sorry.rsp" not in url and stored in html:
                        _LOGGER.info("Re-using HPM session %s", stored)
                        return url, html
                except requests.RequestException:
                    pass
            self.sessionid = None
            url, html = self._request("GET", "")
            if "sorry.rsp" in url:
                raise HpmBusy("The HPM has no free web session (sorry.rsp). Close HPM pages open in a browser "
                              "or wait a few minutes for old sessions to expire")
            if self._needs_login(html):
                if not self.password:
                    raise HpmError("The HPM asks for a login; set username and password in the add-on options")
                url, html = self._submit_password(url, html, self.password, self.username)
                if self._needs_login(html):
                    raise HpmError("HPM login failed; check username and password")
            if self.sessionid is None:
                raise HpmError(f"No session id found on {url}")
            _LOGGER.info("Connected to HPM at %s (session %s)", self.base, self.sessionid)
            if self.code_entry_key and self.access_code:
                self._enter_access_code(self.code_entry_key)  # a new session starts locked again
            return url, html

    def get(self, key: str) -> str:
        """Fetch a page by its key, (re)creating the session when needed."""
        with self.lock:
            for attempt in range(2):
                if self.sessionid is None:
                    self.login()
                try:
                    url, html = self._request("GET", self._with_session(key))
                except requests.HTTPError as err:
                    raise HpmError(f"Fetching {key} failed: {err}") from err  # page problem, session is fine
                except requests.RequestException as err:
                    if attempt:
                        raise HpmError(f"Fetching {key} failed: {err}") from err
                    self.sessionid = None
                    continue
                if self._needs_login(html) and not self._is_login_page(url):
                    # A deeper menu asking for an access code (e.g. the service level).
                    if not self.access_code:
                        raise HpmLocked(f"{key} asks for an access code; set access_code to read it")
                    self._submit_password(url, html, self.access_code)
                    url, html = self._request("GET", self._with_session(key))
                    if self._needs_login(html) and not self._is_login_page(url):
                        raise HpmLocked(f"{key}: the access code was not accepted")
                if self._needs_login(html) or "sessionid" not in html:
                    self.sessionid = None
                    continue
                return html
            raise HpmError(f"Could not fetch {key}; session keeps expiring")

    # -- discovery ---------------------------------------------------------------------
    def crawl(self, max_pages: int = 150, extra_pages: tuple = ()) -> dict[str, Page]:
        with self.lock:
            start_url, start_html = self.login()
            start_key = page_key(start_url) or "v0.rsp"
            pages: dict[str, Page] = {}
            titles: dict[str, str] = {}
            queue = deque([(start_key, start_html)] + [(k, None) for k in extra_pages])
            seen = {start_key, *extra_pages}
            while queue and len(pages) < max_pages:
                key, html = queue.popleft()
                try:
                    html = html if html is not None else self.get(key)
                except HpmError as err:
                    _LOGGER.warning("Skipping page %s: %s", key, err)
                    continue
                if self.on_page:
                    self.on_page(key, html)
                page = parse_page(html, key, titles.get(key, ""))
                pages[key] = page
                _LOGGER.debug("Page %s (%s): %d values, %d links", key, page.title, len(page.datapoints), len(page.links))
                if page.code_entry and self.access_code and page.code_entry not in seen:
                    seen.add(page.code_entry)
                    self._enter_access_code(page.code_entry)
                for link_key, text in page.links:
                    if SKIP_TEXT_RE.search(text):
                        seen.add(link_key)
                        continue
                    if link_key not in seen:
                        seen.add(link_key)
                        titles.setdefault(link_key, text)
                        queue.append((link_key, None))
            if queue:
                _LOGGER.warning("Stopped crawling after max_pages=%d; %d pages not visited", max_pages, len(queue))
            return pages

    def _enter_access_code(self, key: str):
        """Open the code entry page and submit access_code, unlocking deeper menu parameters."""
        self.code_entry_key = key
        self._menu_pos = None
        try:
            url, html = self._request("GET", self._with_session(key))
            self._submit_password(url, html, self.access_code)
            _LOGGER.info("Entered the access code on the HPM")
        except (requests.RequestException, StopIteration) as err:
            _LOGGER.warning("Could not enter the access code via %s: %s", key, err)

    # -- WEB-FB menus --------------------------------------------------------------------
    # Menu navigation is stateful: "menue.rsp?branchnr=B&level=L" selects child B at depth L
    # *below the current position*. A menu page is therefore addressed by its path of branch
    # numbers from the main menu, and reached by replaying that path.
    def _menu_step(self, branchnr: int, level: int) -> str:
        _, html = self._request("GET", self._with_session(f"menue.rsp?branchnr={branchnr}&level={level}"))
        if self._needs_login(html) or "sessionid" not in html:
            raise MenuSessionLost("session lost in WEB-FB menu")
        return html

    @staticmethod
    def _menu_head(html: str) -> tuple[Optional[tuple], str]:
        """The "up" link (branchnr, level) and breadcrumb text from a menu page header."""
        soup = BeautifulSoup(html, "html.parser")
        th = soup.select_one("#mainpane table.mentext th")
        if th is None:
            return None, ""
        up = None
        link = th.find("a", href=True)
        if link:
            query = dict(parse_qsl(urlparse(link["href"]).query))
            if query.get("branchnr", "").isdigit() and query.get("level", "").isdigit():
                up = (int(query["branchnr"]), int(query["level"]))
        return up, _clean(th.get_text(" "))

    def _menu_reset(self) -> str:
        self._menu_pos = None
        self._request("GET", self._with_session("webfb.rsp"))
        html = self._menu_step(1, 0)  # Hauptmenü
        self._menu_pos, self._menu_page = (), html
        return html

    def _menu_goto(self, path: tuple, names: tuple, check: bool) -> str:
        """Move like a person clicking: up via the menu header link to the common parent, then down.

        Every page carries the HPM's menu number ("Menü 1.3.4.x"); with ``check`` each step is
        verified against it (one level deeper/higher, same parent)."""
        pos = self._menu_pos
        html = self._menu_page if pos is not None else self._menu_reset()
        pos = self._menu_pos
        common = 0
        while common < min(len(pos), len(path)) and pos[common] == path[common]:
            common += 1
        while len(pos) > common:
            up, _ = self._menu_head(html)
            if up is None:
                raise HpmError("no way up in the menu")
            before = menu_number(html)
            html = self._menu_step(*up)
            pos = pos[:-1]
            if check and menu_number(html) != before[:-1]:
                raise HpmError(f"went up from {before} but landed on {menu_number(html)}")
            self._menu_pos, self._menu_page = pos, html
        for depth in range(common, len(path)):
            before = menu_number(html)
            html = self._menu_step(path[depth], depth + 1)
            if check:
                now = menu_number(html)
                if len(now) != depth + 1 or now[:-1] != before:
                    raise HpmError(f"expected a sub-menu of {before}, got {now or 'a page without menu number'}: "
                                   f"{_page_hint(html)!r}")
            self._menu_pos, self._menu_page = path[:depth + 1], html
        return html

    def get_menu(self, path: tuple, names: tuple = ()) -> str:
        """Fetch the menu page at ``path`` (branch numbers from the main menu).

        ``names`` (menu titles along the path) are used to check the position; on a mismatch
        the path is replayed from the main menu."""
        with self.lock:
            for attempt in range(3):
                try:
                    if self.sessionid is None:
                        self.login()
                    if attempt:
                        self._menu_pos = None
                    return self._menu_goto(tuple(path), tuple(names), check=True)
                except (requests.RequestException, HpmError) as err:
                    _LOGGER.debug("Menu %s: %s (attempt %d)", path, err, attempt + 1)
                    self._menu_pos = None
                    if attempt == 2:
                        raise HpmError(f"Menu {path} not reachable: {err}") from err
                    if isinstance(err, (requests.RequestException, MenuSessionLost)):
                        self.sessionid = None
            raise HpmError(f"Menu {path} not reachable")

    @staticmethod
    def menu_children(html: str) -> list:
        """Sub-menus listed on a WEB-FB menu page: [(branchnr, text)]."""
        soup = BeautifulSoup(html, "html.parser")
        children = []
        for link in soup.select("#mainpane table.mentext td a[href]"):
            query = dict(parse_qsl(urlparse(link["href"]).query))
            if urlparse(link["href"]).path.lstrip("/") == "menue.rsp" and query.get("branchnr", "").isdigit():
                children.append((int(query["branchnr"]), _clean(link.get_text(" "))))
        return children

    def walk_menus(self, max_pages: int = 150) -> dict[str, Page]:
        """Depth-first walk through the WEB-FB menu tree (GET only). Returns pages with values."""
        with self.lock:
            if self.sessionid is None:
                self.login()
            _, html = self._request("GET", self._with_session("webfb.rsp"))
            start = parse_page(html, "webfb.rsp")
            if start.code_entry and self.access_code:
                self._enter_access_code(start.code_entry)
            self._menu_pos = None
            pages: dict[str, Page] = {}
            visited = 0
            stack = [((), ())]
            while stack and visited < max_pages:
                path, names = stack.pop()
                title = " / ".join(names)
                try:
                    html = self.get_menu(path, names)
                except HpmError as err:
                    _LOGGER.warning("Skipping menu %s: %s", title or "Hauptmenü", err)
                    continue
                visited += 1
                key = "menu:" + "/".join(map(str, path))
                if self.on_page:
                    self.on_page(key, html)
                page = parse_menu_page(html, key, title or "Menü")
                page.datapoints = [dp for dp in page.datapoints if dp.raw]  # e.g. empty fault texts, unused slots
                children = self.menu_children(html)
                _LOGGER.debug("Menu %s (%s): %d values, %d sub-menus", key, page.title, len(page.datapoints), len(children))
                if page.datapoints:
                    pages[key] = page
                for branchnr, text in reversed(children):
                    if MENU_SKIP_RE.search(text):
                        _LOGGER.debug("Not opening menu %s / %s", title, text)
                        continue
                    stack.append((path + (branchnr,), names + (text,)))
            if stack:
                _LOGGER.warning("Stopped the menu walk after max_pages=%d", max_pages)
            return pages

    # -- writing -----------------------------------------------------------------------
    def _open_editor(self, dp: Datapoint) -> tuple[Optional[Editor], str]:
        if dp.change is not None:  # WEB-FB menu row: go to its menu, then click the edit icon
            path = tuple(int(b) for b in dp.page[5:].split("/") if b)
            self.get_menu(path, tuple(dp.menu_title.split(" / ")))
            branchnr, level = dp.change
            try:
                _, html = self._request("GET", self._with_session(f"change.rsp?branchnr={branchnr}&level={level}"))
            finally:
                self._menu_pos = None  # the editor leaves the menu position undefined
            return parse_editor(html), html
        fields = dict(dp.form["fields"])
        fields["sessionid"] = self.sessionid or fields.get("sessionid", "")
        self._menu_pos = None  # the editor may leave the WEB-FB menu somewhere else
        _, html = self._request(dp.form["method"], dp.form["action"], fields)
        return parse_editor(html), html

    def info(self, dp_id: str) -> str:
        """The HPM's detail page for one value (vinfo.rsp)."""
        return self.get(f"vinfo.rsp?id={dp_id}")

    def probe_editor(self, dp: Datapoint) -> tuple[Optional[Editor], str]:
        """Open the editor for a writable value without changing anything."""
        if dp.inline_editor is not None:
            return dp.inline_editor, ""
        with self.lock:
            if self.sessionid is None:
                self.login()
            editor, html = self._open_editor(dp)
            if editor is None and UNAVAILABLE_RE.search(html):
                _LOGGER.info("%s is currently not available on the HPM", dp.id)
            pv = (dp.form or {}).get("fields", {}).get("pv")
            if pv and not pv.startswith("menue"):  # leave the editor the way a browser user would
                try:
                    self.get(pv)
                except HpmError:
                    pass
            return editor, html

    def write(self, dp: Datapoint, value: str) -> str:
        """Store ``value`` (option text for selects) and return the page shown afterwards."""
        with self.lock:
            if self.sessionid is None:
                self.login()
            if dp.inline_editor is not None:
                editor = dp.inline_editor
            else:
                editor, html = self._open_editor(dp)
                if editor is None:
                    self.login()
                    editor, html = self._open_editor(dp)
            if editor is None:
                raise HpmError(f"No editor form found for {dp.id} ({dp.label})")
            data = dict(editor.fields)
            if "sessionid" in data:
                data["sessionid"] = self.sessionid
            if editor.kind == "select":
                match = next((v for v, t in editor.options if t == value or v == value), None)
                if match is None:
                    raise HpmError(f"{value!r} is not an option for {dp.label}: {[t for _, t in editor.options]}")
                data[editor.value_field] = match
            else:
                if editor.kind == "number":
                    number = float(str(value).replace(",", "."))
                    if editor.minimum is not None and number < editor.minimum or editor.maximum is not None and number > editor.maximum:
                        raise HpmError(f"{value} is outside {editor.minimum}..{editor.maximum} for {dp.label}")
                    decimals = dp.decimals
                    if editor.current and "." in editor.current.replace(",", "."):
                        decimals = len(editor.current.replace(",", ".").split(".")[1])
                    value = f"{number:.{decimals}f}"
                    if editor.current and "," in editor.current:
                        value = value.replace(".", ",")
                data[editor.value_field] = value
            _LOGGER.info("Writing %s (%s) = %s", dp.id, dp.label, data[editor.value_field])
            self._menu_pos = None
            _, html = self._request(editor.method, editor.action, data)
            return html
