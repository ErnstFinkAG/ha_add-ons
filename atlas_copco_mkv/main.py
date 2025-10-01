#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Atlas Copco MK5s Touch poller — Python port of the PowerShell script.

Home Assistant friendly entrypoint:
- Reads config.yaml (by default) that can define GLOBAL defaults and a list of DEVICES.
- Runs each device SEQUENTIALLY (no parallelism), printing a table for each.
- Still supports CLI arguments to run a single device (overriding YAML), for backwards-compat.

YAML schema (example):
--------------------------------
timeout: 8                # optional global default
device_name_prefix: ""    # optional; prefix added to each device_name (if present)
devices:
  - controller_host: 10.60.23.12
    device_name: compressor_A
    question_set: GA15VS23A   # GA15VS23A | GA15VP13 | Custom
    custom_question_hex: ""   # required if question_set=Custom
    timeout: 5                # optional per-device override
  - controller_host: 10.60.23.11
    device_name: compressor_B
    question_set: GA15VP13
--------------------------------

Notes:
- No interactive prompts are used when running from YAML; invalid devices are logged and skipped.
- CLI remains available for single-run usage.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from typing import Dict, List, Tuple, Any, Optional, Iterable

# --- Optional YAML support (required for HA multi-device mode) ---
try:
    import yaml  # type: ignore
except Exception as e:
    yaml = None  # We'll error nicely later if multi-device is requested without PyYAML

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
    m = re.match(r"^([0-9A-F]{4})[\.\s]?([0-9A-F]{2})$", k)
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
    \"\"\"case-insensitive map that ALWAYS returns a list of meta dicts\"\"\"
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
    return [{\"Name\": \"?\", \"Unit\": \"?\", \"Encoding\": \"?\", \"Calc\": \"?\"}]


def format_table(rows: List[dict], cols: List[str]) -> str:
    # compute widths
    data = [[(\"\" if r.get(c) is None else str(r.get(c))) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in data)) for i, c in enumerate(cols)]

    def fmt_row(vals: Iterable[str]) -> str:
        return \"  \".join(v.ljust(widths[i]) for i, v in enumerate(vals))

    lines = [fmt_row(cols), fmt_row([\"-\" * w for w in widths])]
    lines += [fmt_row(r) for r in data]
    return \"\\n\".join(lines)


def interactive_select() -> str:
    print(\"[0] GA15VS23A\\n[1] GA15VP13\\n[2] Custom\")
    while True:
        sel = input(\"Select 0/1/2: \").strip()
        if sel in {\"0\", \"1\", \"2\"}:
            break
    return [\"GA15VS23A\", \"GA15VP13\", \"Custom\"][int(sel)]


# ------------------- Core polling for ONE device -------------------
def poll_device(*, controller_host: Optional[str], question_set: Optional[str], custom_question_hex: str, device_name: Optional[str], timeout: int) -> int:
    # select question set
    qset = question_set
    if not qset:
        # In multi-device (HA) mode we should not be interactive; default to GA15VS23A if not specified.
        qset = \"GA15VS23A\"

    if qset in (\"GA15VS23A\", \"GA15VP13\"):
        question_hex = QUESTIONS[qset]
    elif qset == \"Custom\":
        qh = (custom_question_hex or \"\").strip()
        if not qh:
            print(\"[Error] Device missing 'custom_question_hex' for Custom question_set.\", file=sys.stderr)
            return 2
        question_hex = qh
    else:
        print(f\"Unknown QuestionSet: {qset}\", file=sys.stderr)
        return 2

    # auto-select host unless given
    host = controller_host
    if host is None:
        if qset == \"GA15VP13\":
            host = \"10.60.23.11\"
        elif qset == \"GA15VS23A\":
            host = \"10.60.23.12\"

    if host is None:
        print(\"Error: controller_host is required for Custom question set or when auto-selection is not possible.\", file=sys.stderr)
        return 2

    # device label/type
    device_label = device_name or host
    device_type = qset  # e.g., GA15VS23A or GA15VP13

    # sanitize + expand keys
    question_hex = re.sub(r\"\\s+\", \"\", question_hex)
    keys = expand_keys_from_question(question_hex)

    # fetch & sanitize answer
    try:
        answer_raw = post_question(host, question_hex, timeout)
    except Exception as e:
        print(f\"[Error] Request to {host} failed: {e}\", file=sys.stderr)
        return 3

    ans_hex = hex_sanitize(answer_raw)

    # choose meta table
    meta = META_VP13 if qset == \"GA15VP13\" else META_VS23A
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
            if meta_entry.get(\"Name\") == \"?\" and meta_entry.get(\"Encoding\") == \"?\" and meta_entry.get(\"Calc\") == \"?\":
                unknown_keys.add(key)

            calc = meta_entry.get(\"Calc\", \"?\")
            val = eval_calc(calc, u32, lo, hi, key_to_u32, key_to_lo, key_to_hi)

            rows.append({
                \"Device\": device_label,
                \"Type\": device_type,
                \"Key\": key,
                \"Name\": meta_entry.get(\"Name\"),
                \"Raw\": raw,
                \"UInt32\": u32,
                \"LoU16\": lo,
                \"HiU16\": hi,
                \"Encoding\": meta_entry.get(\"Encoding\"),
                \"Calc\": calc,
                \"Value\": None if val is None else (int(val) if val.is_integer() else round(val, 6)),
                \"Unit\": meta_entry.get(\"Unit\"),
            })

    # print rows with Name set (skip unknown '?' like the PS script)
    rows_to_print = [r for r in rows if r.get(\"Name\") and r.get(\"Name\") != \"?\"]
    cols = [\"Device\", \"Type\", \"Key\", \"Name\", \"Raw\", \"UInt32\", \"LoU16\", \"HiU16\", \"Encoding\", \"Calc\", \"Value\", \"Unit\"]
    print(format_table(rows_to_print, cols))

    if unknown_keys:
        print(\"\\n[Info] Unknown keys encountered (no meta): \" + \", \".join(sorted(unknown_keys)))

    return 0


def load_yaml_config(path: str) -> dict:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    if yaml is None:
        raise RuntimeError(\"PyYAML not installed, but a YAML config was requested.\")
    with open(path, \"r\", encoding=\"utf-8\") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(\"Top-level YAML must be a mapping/dict.\")
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=\"Atlas Copco MK5s Touch poller (Python port).\",\n        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            \"\"\"\\
            Host auto-selection (unless overridden with --controller-host):
              - GA15VP13  -> 10.60.23.11
              - GA15VS23A -> 10.60.23.12
            \"\"\"\n        ),
    )

    parser.add_argument(\"--config\", default=\"config.yaml\", help=\"Path to YAML config (default: config.yaml). If present with devices, runs sequentially.\")
    # Single-device CLI args (override YAML or allow standalone single-run)
    parser.add_argument(\"--timeout\", type=int, help=\"HTTP timeout in seconds (default: 5 or YAML).\" )
    parser.add_argument(\"--question-set\", choices=[\"GA15VS23A\", \"GA15VP13\", \"Custom\"], help=\"Which built-in question set to use\")\n    parser.add_argument(\"--custom-question-hex\", default=None, help=\"Used only if --question-set=Custom\")\n    parser.add_argument(\"--controller-host\", default=None, help=\"Controller IP/host (auto-chosen by question set if omitted)\")\n    parser.add_argument(\"--device-name\", default=None, help=\"Label for this device (defaults to controller host)\")\n    args = parser.parse_args(argv)\n\n    # Load YAML (if exists)\n    cfg = load_yaml_config(args.config) if args.config else {}\n\n    # If YAML defines devices -> multi-device sequential mode\n    devices_cfg = []\n    if isinstance(cfg.get(\"devices\"), list) and cfg.get(\"devices\"):\n        global_timeout = cfg.get(\"timeout\", 5)\n        name_prefix = cfg.get(\"device_name_prefix\", \"\")\n        for raw in cfg[\"devices\"]:\n            if not isinstance(raw, dict):\n                print(\"[Warn] Skipping invalid device entry (must be a mapping)\", file=sys.stderr)\n                continue\n            # Build per-device config with overrides\n            d = {\n                \"controller_host\": raw.get(\"controller_host\"),\n                \"question_set\": raw.get(\"question_set\"),\n                \"custom_question_hex\": raw.get(\"custom_question_hex\", \"\"),\n                \"device_name\": (name_prefix + raw.get(\"device_name\", \"\").strip()) if raw.get(\"device_name\") else None,\n                \"timeout\": int(raw.get(\"timeout\", global_timeout)),\n            }\n            devices_cfg.append(d)\n\n        # Run sequentially (NO parallelism)\n        overall_rc = 0\n        for i, d in enumerate(devices_cfg, start=1):\n            label = d.get(\"device_name\") or d.get(\"controller_host\") or f\"device#{i}\"\n            print(\"\\n\" + \"=\"*80)\n            print(f\"[Device {i}] {label}\")\n            print(\"=\"*80)\n            rc = poll_device(\n                controller_host=d.get(\"controller_host\"),\n                question_set=d.get(\"question_set\"),\n                custom_question_hex=d.get(\"custom_question_hex\", \"\"),\n                device_name=d.get(\"device_name\"),\n                timeout=d.get(\"timeout\", 5),\n            )\n            overall_rc = rc if rc != 0 else overall_rc\n        return overall_rc\n\n    # Otherwise: single-device mode (YAML as defaults + CLI overrides)\n    # Compose effective settings\n    def_cfg = {\n        \"timeout\": cfg.get(\"timeout\", 5),\n        \"question_set\": cfg.get(\"question_set\"),\n        \"custom_question_hex\": cfg.get(\"custom_question_hex\", \"\"),\n        \"controller_host\": cfg.get(\"controller_host\"),\n        \"device_name\": cfg.get(\"device_name\"),\n    }\n\n    timeout = args.timeout if args.timeout is not None else def_cfg[\"timeout\"]\n    question_set = args.question_set if args.question_set is not None else def_cfg[\"question_set\"]\n    custom_question_hex = args.custom_question_hex if args.custom_question_hex is not None else def_cfg[\"custom_question_hex\"]\n    controller_host = args.controller_host if args.controller_host is not None else def_cfg[\"controller_host\"]\n    device_name = args.device_name if args.device_name is not None else def_cfg[\"device_name\"]\n\n    # If nothing provided at all, preserve prior interactive behavior\n    if not (question_set or controller_host or device_name or custom_question_hex or args.timeout):\n        # Interactive\n        qset = interactive_select()\n        return poll_device(\n            controller_host=None,\n            question_set=qset,\n            custom_question_hex=\"\",\n            device_name=None,\n            timeout=timeout,\n        )\n\n    # Non-interactive single-run\n    return poll_device(\n        controller_host=controller_host,\n        question_set=question_set,\n        custom_question_hex=custom_question_hex or \"\",\n        device_name=device_name,\n        timeout=timeout,\n    )\n\n\nif __name__ == \"__main__\":\n    sys.exit(main())\n