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
CRAWL_RE = re.compile(r"^/?(v\d+\.rsp|menue\.rsp)$", re.I)
RSP_REF_RE = re.compile(r"""([A-Za-z0-9_]+\.rsp(?:\?[^"'\s<>]*)?)""")
NEVER_CRAWL_RE = re.compile(r"(leave|logout|abmelden|resize|webfb|setgrvalue|reset|delete)", re.I)


class HpmError(Exception):
    pass


class HpmLocked(HpmError):
    """A page asks for an access code that is not configured or was refused."""


class HpmBusy(HpmError):
    """The HPM answered with sorry.rsp: all of its web sessions are in use."""


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


@dataclass
class Page:
    key: str
    title: str
    datapoints: list = field(default_factory=list)
    links: list = field(default_factory=list)  # (key, text)


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
            if CRAWL_RE.match(target.path) and not NEVER_CRAWL_RE.search(href):
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
            if form.get("method", "GET").upper() == "GET" and CRAWL_RE.match(target.path) and not NEVER_CRAWL_RE.search(action):
                page.links.append((page_key(action), _clean(form.get_text(" ")) or page_key(action)))

    # Navigation that is done with JavaScript (onclick, location.href, frames) instead of links
    known = {k for k, _ in page.links}
    for ref in RSP_REF_RE.findall(html.replace("&amp;", "&")):
        ref_key = page_key(ref)
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


def parse_info_name(html: str) -> Optional[str]:
    """Name of a value from its vinfo.rsp page (same headings as the editor, when present)."""
    return _heading_name(BeautifulSoup(html, "html.parser"))


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
        form = next(f for f in soup.find_all("form") if f.find("input", attrs={"type": re.compile("password", re.I)}))
        data = {}
        for inp in form.find_all("input"):
            name, kind = inp.get("name"), inp.get("type", "text").lower()
            if not name:
                continue
            if kind == "password":
                data[name] = secret
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
            return url, html

    def get(self, key: str) -> str:
        """Fetch a page by its key, (re)creating the session when needed."""
        with self.lock:
            for attempt in range(2):
                if self.sessionid is None:
                    self.login()
                try:
                    url, html = self._request("GET", self._with_session(key))
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
                for link_key, text in page.links:
                    if link_key not in seen:
                        seen.add(link_key)
                        titles.setdefault(link_key, text)
                        queue.append((link_key, None))
            if queue:
                _LOGGER.warning("Stopped crawling after max_pages=%d; %d pages not visited", max_pages, len(queue))
            return pages

    # -- writing -----------------------------------------------------------------------
    def _open_editor(self, dp: Datapoint) -> tuple[Optional[Editor], str]:
        fields = dict(dp.form["fields"])
        fields["sessionid"] = self.sessionid or fields.get("sessionid", "")
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
            pv = dp.form["fields"].get("pv")
            if pv:  # leave the editor the way a browser user would
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
            _, html = self._request(editor.method, editor.action, data)
            return html
