# /app/main.py
#
# Panasonic Aquarea TCP dashboard reader (e.g. 10.80.30.70:23)
# - Reads ANSI "status screen" stream over TCP
# - Detects full screens by clear-screen boundaries (ESC[...J)
# - Prints raw dashboard
# - Prints a SECOND table: "HUMAN VALUES - ALL FIELDS"
#   that includes *every* value from the dashboard tables
#   in raw hex + decimal + decoded human form (when known).
#
# Fixes:
# - Header regex now supports tokens with ":" (e.g. SHIFT:OUT_AIR) so DIFF row is parsed correctly.
# - Small sanity check: only treat a line as values if it looks like hex tokens (prevents mispairing).
# - Temp line parsed structurally (word+status pairs + OUT triplet)
# - ER_CODE row realigned (two-byte ER_CODE)

import os
import re
import sys
import time
import json
import socket
from typing import Dict, List, Tuple, Optional

ANSI_CSI_RE = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")
CLEAR_RE = re.compile(r"\x1B\[[0-9;?]*J")

# Header tokens on your screen can contain ":" (SHIFT:OUT_AIR). Allow ":" and "-" too.
HEADER_TOKEN_RE = re.compile(r"^[A-Z0-9_:-]+$")
HEADER_LINE_RE = re.compile(r"^(?:[A-Z0-9_:-]+(?:\s+|$)){2,}$")

HEX_TOKEN_RE = re.compile(r"^[0-9A-Fa-f]+$")


# -------------------- basic helpers --------------------

def load_options() -> dict:
    path = "/data/options.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def strip_ansi(s: str) -> str:
    return ANSI_CSI_RE.sub("", s)


def normalize_text(s: str) -> str:
    s = s.replace("\r", "")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    return s.strip()


def connect(host: str, port: int, timeout_sec: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    sock.connect((host, port))
    sock.settimeout(None)  # blocking recv
    return sock


def looks_like_dashboard(text: str) -> bool:
    return ("R_MODE" in text) and ("ER_CODE" in text)


# -------------------- decoding helpers --------------------

def _hex_to_int(token: str) -> Optional[int]:
    token = token.strip()
    if not token:
        return None
    if not HEX_TOKEN_RE.fullmatch(token):
        return None
    return int(token, 16)


def _int16_signed(x: int) -> int:
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def _int8_signed(x: int) -> int:
    x &= 0xFF
    return x - 0x100 if x & 0x80 else x


def q88_temp_c(word_hex: str) -> Optional[float]:
    x = _hex_to_int(word_hex)
    if x is None:
        return None
    return _int16_signed(x) / 256.0


def _looks_like_values(tokens: List[str]) -> bool:
    """
    A values line is mostly hex tokens (e.g. 00, 0014, F8).
    Accept if at least half of tokens are hex.
    """
    if not tokens:
        return False
    ok = sum(1 for t in tokens if HEX_TOKEN_RE.fullmatch(t))
    return ok >= max(1, len(tokens) // 2)


# -------------------- parsing --------------------

def parse_screen_rows(clean_screen_text: str) -> List[Tuple[List[str], List[str]]]:
    """
    Returns ordered list of (headers[], values[]) pairs for each table row found.
    Applies ER_CODE 2-token alignment fix so the rest of the row maps correctly.

    NOTE: The first temperature line is parsed separately (extract_temp_line()).
    """
    lines = [ln.strip() for ln in clean_screen_text.split("\n") if ln.strip()]
    rows: List[Tuple[List[str], List[str]]] = []

    i = 0
    while i < len(lines):
        hdr_line = lines[i]
        if HEADER_LINE_RE.match(hdr_line) and "DASHBOARD" not in hdr_line:
            headers = hdr_line.split()

            # Ensure header tokens are sane (all tokens match allowed set)
            if not all(HEADER_TOKEN_RE.fullmatch(h) for h in headers):
                i += 1
                continue

            # next non-empty line is values
            j = i + 1
            while j < len(lines):
                vals = lines[j].split()
                if not vals:
                    j += 1
                    continue

                # sanity check so we don't pair wrong lines
                if not _looks_like_values(vals):
                    j += 1
                    continue

                # ER_CODE special: values have one extra token (two-byte ER_CODE)
                if headers and headers[0] == "ER_CODE" and len(vals) == len(headers) + 1:
                    fixed_vals = [f"{vals[0].upper()} {vals[1].upper()}"] + vals[2:]
                    rows.append((headers, fixed_vals))
                    i = j
                    break

                if len(vals) >= len(headers):
                    rows.append((headers, vals[:len(headers)]))
                    i = j
                    break

                j += 1

        i += 1

    return rows


def extract_temp_line(clean_screen_text: str) -> Optional[dict]:
    """
    Parses the first dashboard value line containing temperatures.

    Value line format:
      R_MODE MODE (temp_word temp_status)*N OUT_raw OUT_status OUT_disp

    Returns a dict with:
      r_mode, mode
      pairs: list[(word,status)] in display order
      out_raw, out_status, out_disp
    """
    lines = [ln.strip() for ln in clean_screen_text.split("\n") if ln.strip()]
    for idx, ln in enumerate(lines):
        if ln.startswith("R_MODE") and "OUT_TEMP" in ln:
            if idx + 1 >= len(lines):
                return None
            vals = lines[idx + 1].split()
            if len(vals) < 2 + 3:
                return None

            r_mode = vals[0]
            mode = vals[1]
            tail = vals[2:]

            # last 3 tokens are OUT_TEMP triplet
            out_raw, out_status, out_disp = tail[-3], tail[-2], tail[-1]
            temp_tokens = tail[:-3]

            if len(temp_tokens) < 2 or (len(temp_tokens) % 2) != 0:
                return None

            pairs: List[Tuple[str, str]] = []
            for k in range(0, len(temp_tokens), 2):
                pairs.append((temp_tokens[k], temp_tokens[k + 1]))

            return {
                "r_mode": r_mode,
                "mode": mode,
                "pairs": pairs,
                "out_raw": out_raw,
                "out_status": out_status,
                "out_disp": out_disp,
            }
    return None


# -------------------- field decoding --------------------

def decode_field(name: str, raw: str) -> Tuple[str, str, str]:
    """
    Returns (raw_hex, decimal, human)
    """
    raw = raw.strip()

    # ER_CODE joined as "AA BB"
    if name == "ER_CODE":
        raw_hex = raw.upper()
        human = "No error" if raw_hex == "00 00" else "Fault"
        return (raw_hex, "", human)

    x = _hex_to_int(raw)
    if x is None:
        return (raw, "", "")

    raw_hex = f"0x{raw.upper()}"
    dec_u = str(x)

    # Known Q8.8 temps (16-bit signed)
    if name in {"WATER_TMP_AD", "WATER2_TMP_AD", "IN_TMP_AD", "R1_TMP_AD", "TANK_TMP_AD", "IN_SET"}:
        t = _int16_signed(x) / 256.0
        return (raw_hex, dec_u, f"{t:.2f} °C")

    # Integer °C setpoints
    if name in {"REMO_SET", "TANK_SET", "STERTMP"}:
        return (raw_hex, dec_u, f"{x} °C")

    # DIFF: signed 8-bit
    if name == "DIFF":
        s = _int8_signed(x)
        return (raw_hex, dec_u, f"{s} (signed)")

    # Pump duty
    if name == "PM_DUTY":
        return (raw_hex, dec_u, f"{x} %")

    # Compressor frequency
    if name == "COMPHZ":
        return (raw_hex, dec_u, f"{x} Hz")

    # Power values (you confirmed HPOWER=W)
    if name in {"HPOWER", "CPOWER", "TPOWER"}:
        return (raw_hex, dec_u, f"{x} W ({x/1000.0:.3f} kW)")

    # Booleans
    if name in {"CMP_REQ", "W_PUMP", "COOL", "FREEZE"}:
        return (raw_hex, dec_u, "ON" if x != 0 else "OFF")

    # Bitfields
    if name in {"OUTPUT", "INPUT", "FLAG0", "FLAG1", "FLAG2", "FLAG3"}:
        return (raw_hex, dec_u, "bitfield")

    # Otherwise unknown/enum
    if name in {
        "OD_LPT", "OD_HPT", "WT_LPT", "WT_HPT", "HOFF", "HEATOD",
        "C_SET", "F_SEL", "H_TIME", "T_TIME", "TDELAY", "STERTM",
        "O_STATUS", "O_PIPE", "O_CUR", "O_DISC", "O_VAL",
        "PM_TAP", "STARTUP", "SHIFT:OUT_AIR"
    }:
        return (raw_hex, dec_u, "raw/enum")

    return (raw_hex, dec_u, "")


# -------------------- output --------------------

def print_human_table(clean_screen_text: str) -> None:
    out_rows: List[Tuple[str, str, str, str]] = []  # name, raw_hex, dec, human

    def add(name: str, raw_hex: str, dec: str, human: str):
        out_rows.append((name, raw_hex, dec, human))

    # ---- 1) Special: temperature line ----
    temp = extract_temp_line(clean_screen_text)
    if temp:
        pairs = temp["pairs"]

        # Map ordered pairs to named channels
        named: Dict[str, Tuple[str, str]] = {}
        if len(pairs) >= 1:
            named["WATER_TMP_AD"] = pairs[0]
        if len(pairs) >= 2:
            named["WATER2_TMP_AD"] = pairs[1]
        if len(pairs) >= 3:
            named["IN_TMP_AD"] = pairs[2]

        if len(pairs) == 5:
            named["R1_TMP_AD"] = pairs[3]
            named["TANK_TMP_AD"] = pairs[4]
        elif len(pairs) == 4:
            named["R1_TMP_AD"] = pairs[2]  # mirror IN
            named["TANK_TMP_AD"] = pairs[3]
        else:
            if len(pairs) >= 4:
                named["R1_TMP_AD"] = pairs[3]
            if len(pairs) >= 5:
                named["TANK_TMP_AD"] = pairs[4]

        for field in ["WATER_TMP_AD", "WATER2_TMP_AD", "IN_TMP_AD", "R1_TMP_AD", "TANK_TMP_AD"]:
            if field in named:
                word, status = named[field]
                rh, dec, human = decode_field(field, word)
                add(field, rh, dec, human)

                st_i = _hex_to_int(status)
                add(field + "_STATUS", f"0x{status.upper()}", str(st_i) if st_i is not None else "", "status/quality")

        # OUT_TEMP triplet
        out_raw = temp["out_raw"]
        out_status = temp["out_status"]
        out_disp = temp["out_disp"]

        or_i = _hex_to_int(out_raw)
        os_i = _hex_to_int(out_status)
        od_i = _hex_to_int(out_disp)

        add("OUT_TEMP_RAW", f"0x{out_raw.upper()}", str(or_i) if or_i is not None else "", "raw slot")
        add("OUT_TEMP_STATUS", f"0x{out_status.upper()}", str(os_i) if os_i is not None else "", "raw validity/status")

        if os_i == 0xFF and od_i is not None:
            add("OUT_TEMP_DISPLAY", f"0x{out_disp.upper()}", str(od_i), f"{od_i} °C (rounded)")
        else:
            t_out = q88_temp_c(out_raw)
            if t_out is not None:
                add("OUT_TEMP_DECODED", f"0x{out_raw.upper()}", str(or_i) if or_i is not None else "", f"{t_out:.2f} °C")

    # ---- 2) All other rows (everything) ----
    for headers, values in parse_screen_rows(clean_screen_text):
        # Skip the temp row header; we already handled it properly
        if headers and headers[0] == "R_MODE" and "OUT_TEMP" in headers:
            continue

        for h, v in zip(headers, values):
            raw_hex, dec, human = decode_field(h, v)
            add(h, raw_hex, dec, human)

    if not out_rows:
        return

    name_w = max(len(r[0]) for r in out_rows)
    raw_w = max(len(r[1]) for r in out_rows)
    dec_w = max(len(r[2]) for r in out_rows)

    print("[HUMAN VALUES - ALL FIELDS]")
    print("-" * (name_w + raw_w + dec_w + 12))
    for name, raw_hex, dec, human in out_rows:
        human_str = f"  {human}" if human else ""
        print(f"{name:<{name_w}}  {raw_hex:<{raw_w}}  {dec:>{dec_w}}{human_str}")
    print("-" * (name_w + raw_w + dec_w + 12))


def print_screen_with_human(screen_text: str, ts_fmt: str, do_strip: bool) -> None:
    clean = strip_ansi(screen_text)
    clean = normalize_text(clean)
    if not clean:
        return

    shown = clean if do_strip else normalize_text(screen_text)
    timestamp = time.strftime(ts_fmt, time.localtime())

    print("\n" + "=" * 60)
    print(f"[DASHBOARD] {timestamp}")
    print(shown)
    print()
    print_human_table(clean)
    print("=" * 60 + "\n")
    sys.stdout.flush()


# -------------------- main loop --------------------

def main():
    opt = load_options()

    host = opt.get("host", "10.80.30.70")
    port = int(opt.get("port", 23))
    sock_timeout = int(opt.get("socket_timeout_sec", 1))

    do_strip_ansi = bool(opt.get("strip_ansi", True))
    max_buffer_kb = int(opt.get("max_buffer_kb", 256))
    ts_fmt = opt.get("timestamp_format", "%Y-%m-%d %H:%M:%S")

    max_chars = max_buffer_kb * 1024

    print("[INFO] Panasonic Aquarea TCP dashboard reader")
    print(f"[INFO] Connecting to {host}:{port}")
    sys.stdout.flush()

    sock = None
    stream_buf = ""
    current_screen = ""
    synced = False  # drop first boundary-delimited screen after connect

    while True:
        try:
            if sock is None:
                print("[INFO] Connecting...")
                sys.stdout.flush()
                sock = connect(host, port, sock_timeout)
                print("[INFO] Connected")
                sys.stdout.flush()

                stream_buf = ""
                current_screen = ""
                synced = False

            data = sock.recv(4096)
            if not data:
                raise ConnectionError("Socket closed by peer")

            chunk = data.decode("latin-1", errors="replace")
            stream_buf += chunk

            # prevent runaway
            if len(stream_buf) > max_chars:
                stream_buf = stream_buf[-max_chars:]

            # split on clear-screen boundaries
            parts = CLEAR_RE.split(stream_buf)

            if len(parts) >= 2:
                for seg in parts[:-1]:
                    current_screen += seg

                    if looks_like_dashboard(current_screen):
                        if synced:
                            print_screen_with_human(current_screen, ts_fmt, do_strip_ansi)
                        else:
                            synced = True

                    current_screen = ""

                stream_buf = parts[-1]
            else:
                if len(stream_buf) > (max_chars // 2):
                    current_screen += stream_buf
                    stream_buf = ""

        except Exception as e:
            print(f"[WARN] Connection issue: {e}")
            sys.stdout.flush()
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
            sock = None
            time.sleep(2)


if __name__ == "__main__":
    main()
