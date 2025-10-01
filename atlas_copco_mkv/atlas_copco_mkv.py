#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Atlas Copco MK5s Touch poller — Python port of the PowerShell script.

- Interactive or CLI-driven selection of question set: GA15VS23A, GA15VP13, or Custom
- Auto-selects default ControllerHost based on question set unless overridden
- Posts QUESTION hex to http://<host>/cgi-bin/mkv.cgi
- Parses big-endian UInt32s (8 hex chars per key in reply, keyed to 6-hex "questions")
- Builds rows in the *original question order*
- Evaluates Calc expressions with support for:
    - UInt32 / LoU16 / HiU16 of the *current* key
    - Cross-key refs: UInt32ofHHHH.HH, LoU16ofHHHH.HH, HiU16ofHHHH.HH
    - Safe arithmetic: digits, ., + - * / ( ) and whitespace only
- Prints a compact table (no third-party deps)
- Adds leading columns: Device, Type (before Key)
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from typing import Dict, List, Tuple, Any, Optional, Iterable

# --- HTTP client: try requests, else stdlib urllib ---
try:
    import requests  # type: ignore

    def post_question(host: str, qhex: str, timeout_sec: int) -> str:
        uri = f"http://{host}/cgi-bin/mkv.cgi"
        try:
            resp = requests.post(
                uri,
                data={"QUESTION": qhex},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=timeout_sec,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}") from e

except ModuleNotFoundError:
    import urllib.request
    import urllib.parse

    def post_question(host: str, qhex: str, timeout_sec: int) -> str:
        uri = f"http://{host}/cgi-bin/mkv.cgi"
        data = urllib.parse.urlencode({"QUESTION": qhex}).encode("ascii")
        req = urllib.request.Request(
            uri,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}") from e


# === Built-in QUESTION strings (output preserves this order) ===
QUESTIONS: Dict[str, str] = {
    "GA15VS23A": (
        "30020130022430022630022730022a30026630032130032230032e30032f300330300701300703300704300705"
        "30070630070730070830070930070b30070c30070d30070e30070f30071730071830071b300725300726300727"
        "30074330074c30074d30075430075530075630075730210130210530210a30220130220a30051f300520300521"
        "30052730052830052930052a300e03300e04300e05300e2a300ef3310e23310e27310e2b310e3b311301311303"
        "31130431130531130731130831130931130a31130b31130c31130d31130e31130f311310311311311312311313"
        "31131431131531131631131731131831131931131a31131b31131c31131d31131e31131f311320311321311322"
        "31132331132431132531132631132731132831132931132a31132b31132c31132d31132e31132f311330311331"
        "31133231133331133431133531133631133731133831133931133a31133b31133c31133d31133e31133f311340"
        "31134131134231134331134431134531134631134731134831134931134a31134b31134c31134d31134e31134f"
        "31135031135131135231135331135431135531135631135731135831135931135a31135b31135c31135d31135e"
        "31135f311360311361311362311363311364311365311366311367311401311402311403311404311405311406"
        "31140731140831140931140a31140b31140c31140d31140e31140f311410311411311412300901300906300911"
        "300907300912300909300914300108"
    ),
    "GA15VP13": (
        "30020130020330020530020830030130030230030a300701300703300704300705300706300707300708300709"
        "30070b30070c30070d30070e30070f30071430071530071830072230072330072430210130210530210a300501"
        "300502300504300505300507300508300509300e03300e04300e2a300e88311301311303311304311305311307"
        "31130831130931130a31130b31130c31130d31130e31130f311310311311311312311313311314311315311316"
        "31131731131831131931131a31131b31131c31131d31131e31131f311320311321311322311323311324311325"
        "31132631132731132831132931132a31132b31132c31132d31132e31132f311330311331311332311333311334"
        "31133531133631133731133831133931133a31133b31133c31133d31133e31133f311340311341311342311343"
        "31134431134531134631134731134831134931134a31134b31134c31134d31134e31134f311350311351311352"
        "31135331135431135531135631135731135831135931135a31135b31135c31135d31135e31135f311360311361"
        "311362311363311364311365311366311367311401311402311403311404311405311406311407311408311409"
        "31140a31140b31140c31140d31140e31140f311410311411311412300901300906300911300907300912300108"
    ),
}

# === Helpers ===
def normalize_key(k: str) -> str:
    if not k:
        return ""
    k = k.strip().upper()
    m = re.match(r"^([0-9A-F]{4})[\\.\\s]?([0-9A-F]{2})$", k)
    return f"{m.group(1)}.{m.group(2)}" if m else k


def expand_keys_from_question(qhex: str) -> List[str]:
    q = re.sub(r"\\s+", "", qhex or "").upper()
    keys: List[str] = []
    for i in range(0, len(q), 6):
        keys.append(f"{q[i:i+4]}.{q[i+4:i+6]}")
    return keys


def hex_sanitize(s: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", s or "").upper()


def hex_slice(hexstr: str, offset: int, length: int) -> str:
    if offset < 0 or offset + length > len(hexstr):
        return ""
    return hexstr[offset : offset + length].upper()


def hex_to_uint32_be(hex8: str) -> Optional[int]:
    if not hex8 or len(hex8) != 8 or not re.match(r"^[0-9A-F]{8}$", hex8):
        return None
    return int(hex8, 16)


def lo_u16(u32: Optional[int]) -> Optional[int]:
    return None if u32 is None else (u32 & 0xFFFF)


def hi_u16(u32: Optional[int]) -> Optional[int]:
    return None if u32 is None else (u32 >> 16)


# --- Eval with support for cross-key refs like UInt32of3007.01 / LoU16ofABCD.EF ---
def resolve_external_refs(
    expr: str,
    key_to_u32: Dict[str, Optional[int]],
    key_to_lo: Dict[str, Optional[int]],
    key_to_hi: Dict[str, Optional[int]],
) -> Tuple[str, bool]:
    ok = True

    def sub_generic(m: re.Match, d: Dict[str, Optional[int]]) -> str:
        nonlocal ok
        key = f"{m.group(1)}.{m.group(2)}".upper()
        val = d.get(key, None)
        if val is None:
            ok = False
            return ""
        return str(val)

    expr = re.sub(r"\\bUInt32of([0-9A-F]{4})\\.([0-9A-F]{2})\\b", lambda m: sub_generic(m, key_to_u32), expr)
    expr = re.sub(r"\\bLoU16of([0-9A-F]{4})\\.([0-9A-F]{2})\\b",  lambda m: sub_generic(m, key_to_lo),  expr)
    expr = re.sub(r"\\bHiU16of([0-9A-F]{4})\\.([0-9A-F]{2})\\b",  lambda m: sub_generic(m, key_to_hi),  expr)
    return expr, ok


def eval_calc(
    calc: str,
    u32: Optional[int],
    lo: Optional[int],
    hi: Optional[int],
    key_to_u32: Dict[str, Optional[int]],
    key_to_lo: Dict[str, Optional[int]],
    key_to_hi: Dict[str, Optional[int]],
) -> Optional[float]:
    if not calc or calc.strip() == "?":
        return None

    expr = calc
    expr = re.sub(r"\\bUInt32\\b", str(u32) if u32 is not None else "", expr)
    expr = re.sub(r"\\bLoU16\\b",  str(lo) if lo is not None else "", expr)
    expr = re.sub(r"\\bHiU16\\b",  str(hi) if hi is not None else "", expr)

    expr, ok = resolve_external_refs(expr, key_to_u32, key_to_lo, key_to_hi)
    if not ok:
        return None

    if not re.match(r"^[0-9\\.\\+\\-\\*\\/\\(\\)\\s]+$", expr):
        return None
    try:
        return float(eval(expr, {"__builtins__": None}, {}))
    except Exception:
        return None


# === Model-specific lookup tables ===

# ------ GA15VP13 ------
META_VP13: Dict[str, Any] = {
    "3002.01": {"Name": "Compressor Outlet", "Unit": "bar", "Encoding": "HiU16", "Calc": "HiU16/1000"},
    "3002.03": {"Name": "Element Outlet", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.05": {"Name": "Ambient Air", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.08": {"Name": "Controller Temperature", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3021.01": [
        {"Name": "Motor requested rpm", "Unit": "rpm", "Encoding": "LoU16", "Calc": "LoU16"},
        {"Name": "Motor actual rpm",    "Unit": "rpm", "Encoding": "HiU16", "Calc": "HiU16"},
    ],
    "3007.01": {"Name": "Running Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.03": {"Name": "Motor Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.04": {"Name": "Load Relay", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.05": {"Name": "VSD 1-20", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.06": {"Name": "VSD 20-40", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.07": {"Name": "VSD 40-60", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.08": {"Name": "VSD 60-80", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.09": {"Name": "VSD 80-100", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.0B": {"Name": "Fan Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0C": {"Name": "Accumulated Volume", "Unit": "m3", "Encoding": "UInt32", "Calc": "UInt32*1000"},
    "3007.0D": {"Name": "Module Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.0E": {"Name": "Emergency Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0F": {"Name": "Direct Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.14": {"Name": "Recirculation Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.15": {"Name": "Recirculation Failures", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.18": {"Name": "Low Load Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.22": {"Name": "Available Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.23": {"Name": "Unavailable Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.24": {"Name": "Emergency Stop Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3021.05": {"Name": "Motor amperage", "Unit": "A", "Encoding": "HiU16", "Calc": "HiU16"},
    "3021.0A": {"Name": "Flow", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32"},
    "3113.50": {"Name": "Service A 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.51": {"Name": "Service A 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.52": {"Name": "Service B 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.53": {"Name": "Service B 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.54": {"Name": "Machine Status", "Unit": "code", "Encoding": "UInt32", "Calc": "UInt32"},
}

# ------ GA15VS23A ------
META_VS23A: Dict[str, Any] = {
    "3002.01": {"Name": "Controller Temperature", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.24": {"Name": "Compressor Outlet", "Unit": "bar", "Encoding": "HiU16", "Calc": "HiU16/1000"},
    "3002.26": {"Name": "Ambient Air", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.27": {"Name": "Relative Humidity", "Unit": "%", "Encoding": "HiU16", "Calc": "HiU16"},
    "3002.2A": {"Name": "Element Outlet", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3002.66": {"Name": "Aftercooler drain PCB Temperature", "Unit": "°C", "Encoding": "HiU16", "Calc": "HiU16/10"},
    "3021.01": [
        {"Name": "Motor requested rpm", "Unit": "rpm", "Encoding": "LoU16", "Calc": "LoU16"},
        {"Name": "Motor actual rpm",    "Unit": "rpm", "Encoding": "HiU16", "Calc": "HiU16"},
    ],
    "3022.01": [
        {"Name": "Fan Motor requested rpm", "Unit": "rpm", "Encoding": "LoU16", "Calc": "LoU16"},
        {"Name": "Fan Motor actual rpm",    "Unit": "rpm", "Encoding": "HiU16", "Calc": "HiU16"},
    ],
    "3007.01": {"Name": "Running Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.03": {"Name": "Motor Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.04": {"Name": "Load Relay", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.05": {"Name": "VSD 1-20", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.06": {"Name": "VSD 20-40", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.07": {"Name": "VSD 40-60", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.08": {"Name": "VSD 60-80", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.09": {"Name": "VSD 80-100", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32/UInt32of3007.01*100"},
    "3007.0B": {"Name": "Fan Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0C": {"Name": "Accumulated Volume", "Unit": "m3", "Encoding": "UInt32", "Calc": "UInt32*1000"},
    "3007.0D": {"Name": "Module Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.0E": {"Name": "Emergency Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.0F": {"Name": "Direct Stops", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.17": {"Name": "Recirculation Starts", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.18": {"Name": "Recirculation Failures", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.1B": {"Name": "Low Load Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.25": {"Name": "Available Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.26": {"Name": "Unavailable Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.27": {"Name": "Emergency Stop Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.43": {"Name": "Display Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.4C": {"Name": "Boostflow Hours", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.4D": {"Name": "Boostflow Activations", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.54": {"Name": "Emergency Stops During Running", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.55": {"Name": "Drain 1 Operation Time", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3007.56": {"Name": "Drain 1 number of switching actions", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3007.57": {"Name": "Drain 1 number of manual drainings", "Unit": "count", "Encoding": "UInt32", "Calc": "UInt32"},
    "3021.05": {"Name": "Flow", "Unit": "%", "Encoding": "UInt32", "Calc": "UInt32"},
    "3021.0A": {"Name": "Motor amperage", "Unit": "A", "Encoding": "HiU16", "Calc": "HiU16"},
    "3022.0A": {"Name": "Fan Motor amperage", "Unit": "A", "Encoding": "HiU16", "Calc": "HiU16"},
    "3113.50": {"Name": "Service A 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.51": {"Name": "Service A 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.52": {"Name": "Service B 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.53": {"Name": "Service B 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.54": {"Name": "Service D 1", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.55": {"Name": "Service D 2", "Unit": "h", "Encoding": "UInt32", "Calc": "UInt32/3600"},
    "3113.56": {"Name": "Machine Status", "Unit": "code", "Encoding": "UInt32", "Calc": "UInt32"},
}

def build_meta_lookup(meta: Dict[str, Any]) -> Dict[str, List[dict]]:
    """case-insensitive map that ALWAYS returns a list of meta dicts"""
    table: Dict[str, List[dict]] = {}
    for k, v in meta.items():
        nk = normalize_key(k)
        if isinstance(v, list):
            table[nk] = [dict(x) for x in v]
        else:
            table[nk] = [dict(v)]
    return table


def get_meta_for_key(lookup: Dict[str, List[dict]], key: str) -> List[dict]:
    nk = normalize_key(key)
    if nk in lookup:
        return lookup[nk]
    return [{"Name": "?", "Unit": "?", "Encoding": "?", "Calc": "?"}]


def format_table(rows: List[dict], cols: List[str]) -> str:
    # compute widths
    data = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in data)) for i, c in enumerate(cols)]

    def fmt_row(vals: Iterable[str]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(vals))

    lines = [fmt_row(cols), fmt_row(["-" * w for w in widths])]
    lines += [fmt_row(r) for r in data]
    return "\n".join(lines)


def interactive_select() -> str:
    print("[0] GA15VS23A\\n[1] GA15VP13\\n[2] Custom")
    while True:
        sel = input("Select 0/1/2: ").strip()
        if sel in {"0", "1", "2"}:
            break
    return ["GA15VS23A", "GA15VP13", "Custom"][int(sel)]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atlas Copco MK5s Touch poller (Python port).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Host auto-selection (unless overridden with --controller-host):
              - GA15VP13  -> 10.60.23.11
              - GA15VS23A -> 10.60.23.12
            """
        ),
    )
    parser.add_argument("--timeout", type=int, default=5, help="HTTP timeout in seconds (default: 5)")
    parser.add_argument("--question-set", choices=["GA15VS23A", "GA15VP13", "Custom"], help="Which built-in question set to use")
    parser.add_argument("--custom-question-hex", default="", help="Used only if --question-set=Custom")
    parser.add_argument("--controller-host", default=None, help="Controller IP/host (auto-chosen by question set if omitted)")
    parser.add_argument("--device-name", default=None, help="Label for this device (defaults to controller host)")
    args = parser.parse_args(argv)

    # select question set
    qset = args.question_set or interactive_select()

    if qset in ("GA15VS23A", "GA15VP13"):
        question_hex = QUESTIONS[qset]
    elif qset == "Custom":
        qh = args.custom_question_hex.strip()
        if not qh:
            print("Paste your question hex (whitespace OK):")
            qh = input("QuestionHex: ").strip()
        if not qh:
            print("No custom question hex provided.", file=sys.stderr)
            return 2
        question_hex = qh
    else:
        print(f"Unknown QuestionSet: {qset}", file=sys.stderr)
        return 2

    # auto-select host unless given
    host = args.controller_host
    if host is None:
        if qset == "GA15VP13":
            host = "10.60.23.11"
        elif qset == "GA15VS23A":
            host = "10.60.23.12"

    if host is None:
        print("Error: --controller-host is required for Custom question set.", file=sys.stderr)
        return 2

    # device label/type
    device_name = args.device_name or host
    device_type = qset  # e.g., GA15VS23A or GA15VP13

    # sanitize + expand keys
    question_hex = re.sub(r"\\s+", "", question_hex)
    keys = expand_keys_from_question(question_hex)

    # fetch & sanitize answer
    answer_raw = post_question(host, question_hex, args.timeout)
    ans_hex = hex_sanitize(answer_raw)

    # choose meta table
    meta = META_VP13 if qset == "GA15VP13" else META_VS23A
    meta_lookup = build_meta_lookup(meta)

    # pre-index all raw values for cross-key calcs
    key_to_u32: Dict[str, Optional[int]] = {}
    key_to_lo: Dict[str, Optional[int]] = {}
    key_to_hi: Dict[str, Optional[int]] = {}

    for i, k in enumerate(keys):
        nk = normalize_key(k)
        raw = hex_slice(ans_hex, i * 8, 8)
        u32 = hex_to_uint32_be(raw)
        lo = lo_u16(u32)
        hi = hi_u16(u32)
        key_to_u32[nk] = u32
        key_to_lo[nk] = lo
        key_to_hi[nk] = hi

    # build rows in original order
    rows: List[dict] = []
    unknown_keys: set[str] = set()

    for idx, k in enumerate(keys):
        key = normalize_key(k)
        raw = hex_slice(ans_hex, idx * 8, 8)
        u32 = key_to_u32.get(key)
        lo = key_to_lo.get(key)
        hi = key_to_hi.get(key)

        metas = get_meta_for_key(meta_lookup, key)
        for meta_entry in metas:
            if meta_entry.get("Name") == "?" and meta_entry.get("Encoding") == "?" and meta_entry.get("Calc") == "?":
                unknown_keys.add(key)

            calc = meta_entry.get("Calc", "?")
            val = eval_calc(calc, u32, lo, hi, key_to_u32, key_to_lo, key_to_hi)

            rows.append({
                "Device": device_name,
                "Type": device_type,
                "Key": key,
                "Name": meta_entry.get("Name"),
                "Raw": raw,
                "UInt32": u32,
                "LoU16": lo,
                "HiU16": hi,
                "Encoding": meta_entry.get("Encoding"),
                "Calc": calc,
                "Value": None if val is None else (int(val) if val.is_integer() else round(val, 6)),
                "Unit": meta_entry.get("Unit"),
            })

    # print rows with Name set (skip unknown '?' like the PS script)
    rows_to_print = [r for r in rows if r.get("Name") and r.get("Name") != "?"]
    cols = ["Device", "Type", "Key", "Name", "Raw", "UInt32", "LoU16", "HiU16", "Encoding", "Calc", "Value", "Unit"]
    print(format_table(rows_to_print, cols))

    if unknown_keys:
        print("\\n[Info] Unknown keys encountered (no meta): " + ", ".join(sorted(unknown_keys)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
