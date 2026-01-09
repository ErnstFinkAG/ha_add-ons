# /app/main.py
#
# Panasonic Aquarea TCP dashboard reader (TCP stream, e.g. 10.80.30.70:23)
# - Reads ANSI "status screen" stream over TCP
# - Detects full screens by clear-screen boundaries (ESC[...J)
# - Prints raw dashboard
# - Prints an additional "HUMAN VALUES" table with proper decoding
#
# FIXES INCLUDED:
# 1) Temperature line parsing:
#    First dashboard value line contains extra status bytes not present in the header.
#    We parse it structurally as:
#       R_MODE MODE (temp_word temp_status)*N  OUT_raw OUT_status OUT_disp
#    with N usually 4 or 5 depending on configuration.
#
# 2) ER_CODE table alignment:
#    ER_CODE is displayed as TWO tokens ("00 00") but header has one column "ER_CODE".
#    We detect and realign so O_STATUS..TPOWER are mapped correctly.
#
# 3) Human table now includes COMPHZ/HPOWER/etc.

import os
import re
import sys
import time
import json
import socket
from typing import Dict, List, Tuple, Optional

ANSI_CSI_RE = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")
CLEAR_RE = re.compile(r"\x1B\[[0-9;?]*J")


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
    sock.settimeout(None)
    return sock


def looks_like_dashboard(text: str) -> bool:
    return ("R_MODE" in text) and ("ER_CODE" in text)


# ---------- decoding helpers ----------

def _hex_to_int(token: str) -> Optional[int]:
    token = token.strip()
    if not token:
        return None
    if not re.fullmatch(r"[0-9A-Fa-f]+", token):
        return None
    return int(token, 16)


def _int16_signed(x: int) -> int:
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def q88_temp_c(word_hex: str) -> Optional[float]:
    x = _hex_to_int(word_hex)
    if x is None:
        return None
    return _int16_signed(x) / 256.0


def parse_screen_tables(screen_text: str) -> Dict[str, str]:
    """
    Generic parser: header line -> next value line mapping.

    Special handling:
      - ER_CODE row: value line has one extra token because ER_CODE is two bytes (e.g., "00 00").
        We realign to keep O_STATUS..TPOWER correct.

    NOTE:
      - The first temperature line is parsed separately in extract_temp_line().
    """
    lines = [ln.strip() for ln in screen_text.split("\n") if ln.strip()]
    out: Dict[str, str] = {}

    header_re = re.compile(r"^(?:[A-Z0-9_]+(?:\s+|$)){2,}$")

    i = 0
    while i < len(lines):
        header = lines[i]
        if header_re.match(header) and "DASHBOARD" not in header:
            headers = header.split()

            j = i + 1
            while j < len(lines):
                vals = lines[j].split()
                if not vals:
                    j += 1
                    continue

                # ER_CODE is printed as two tokens, so values are one token longer than headers
                if headers and headers[0] == "ER_CODE" and len(vals) == len(headers) + 1:
                    out["ER_CODE"] = f"{vals[0].upper()} {vals[1].upper()}"
                    shifted = vals[2:]
                    for h, v in zip(headers[1:], shifted):
                        out[h] = v
                    i = j
                    break

                # Normal case
                if len(vals) >= len(headers):
                    for h, v in zip(headers, vals):
                        out[h] = v
                    i = j
                    break

                j += 1

        i += 1

    return out


def extract_temp_line(clean_screen_text: str) -> Optional[dict]:
    """
    Dynamically parses the first dashboard value line.

    Value line format:
      R_MODE MODE  (temp_word temp_status)*N   OUT_raw OUT_status OUT_disp

    OUT_disp is the rounded outdoor °C when OUT_status == FF (observed on your system).

    Returns dict with keys:
      r_mode, mode,
      water_word, water_status,
      water2_word, water2_status,
      in_word, in_status,
      r1_word, r1_status,
      tank_word, tank_status,
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

            out_raw, out_status, out_disp = tail[-3], tail[-2], tail[-1]
            temp_tokens = tail[:-3]

            if len(temp_tokens) < 2 or (len(temp_tokens) % 2) != 0:
                return None

            pairs: List[Tuple[str, str]] = []
            for k in range(0, len(temp_tokens), 2):
                pairs.append((temp_tokens[k], temp_tokens[k + 1]))

            res = {
                "r_mode": r_mode,
                "mode": mode,
                "water_word": "", "water_status": "",
                "water2_word": "", "water2_status": "",
                "in_word": "", "in_status": "",
                "r1_word": "", "r1_status": "",
                "tank_word": "", "tank_status": "",
                "out_raw": out_raw, "out_status": out_status, "out_disp": out_disp,
            }

            if len(pairs) >= 1:
                res["water_word"], res["water_status"] = pairs[0]
            if len(pairs) >= 2:
                res["water2_word"], res["water2_status"] = pairs[1]
            if len(pairs) >= 3:
                res["in_word"], res["in_status"] = pairs[2]

            if len(pairs) == 5:
                res["r1_word"], res["r1_status"] = pairs[3]
                res["tank_word"], res["tank_status"] = pairs[4]
            elif len(pairs) == 4:
                # Common in your output: IN is duplicated for R1, 4th pair is TANK
                res["r1_word"], res["r1_status"] = pairs[2]   # mirror IN
                res["tank_word"], res["tank_status"] = pairs[3]
            else:
                if len(pairs) >= 4:
                    res["r1_word"], res["r1_status"] = pairs[3]
                if len(pairs) >= 5:
                    res["tank_word"], res["tank_status"] = pairs[4]

            return res

    return None


def print_human_table(clean_screen_text: str) -> None:
    kv = parse_screen_tables(clean_screen_text)
    rows: List[Tuple[str, str, str]] = []

    def add_row(name: str, val: str, unit: str = ""):
        rows.append((name, val, unit))

    def add_temp(label: str, word_hex: str, status_hex: str):
        if not word_hex or not status_hex:
            return
        t = q88_temp_c(word_hex)
        if t is not None:
            add_row(label, f"{t:.2f}", "°C")
        add_row(label + " status", f"0x{status_hex.upper()}", "")

    # ---- Temps (structural parse) ----
    temp = extract_temp_line(clean_screen_text)
    if temp:
        add_temp("Flow temp (WATER_TMP)", temp["water_word"], temp["water_status"])
        add_temp("Return temp (WATER2_TMP)", temp["water2_word"], temp["water2_status"])
        add_temp("Indoor/internal temp (IN_TMP)", temp["in_word"], temp["in_status"])
        add_temp("Zone/loop temp (R1_TMP)", temp["r1_word"], temp["r1_status"])
        add_temp("Tank temp (TANK_TMP)", temp["tank_word"], temp["tank_status"])

        # Outdoor (raw invalid -> display rounded °C)
        add_row("Outdoor raw word", f"0x{temp['out_raw'].upper()}", "")
        add_row("Outdoor raw status", f"0x{temp['out_status'].upper()}", "")
        st = _hex_to_int(temp["out_status"])
        disp = _hex_to_int(temp["out_disp"])
        if st == 0xFF and disp is not None:
            add_row("Outdoor temperature (rounded)", str(disp), "°C")
        else:
            t_out = q88_temp_c(temp["out_raw"])
            if t_out is not None:
                add_row("Outdoor temperature (raw decoded)", f"{t_out:.2f}", "°C")

    # ---- Setpoints ----
    remo = _hex_to_int(kv.get("REMO_SET", ""))
    if remo is not None:
        add_row("Remote setpoint (REMO_SET)", str(remo), "°C")

    in_set = q88_temp_c(kv.get("IN_SET", ""))
    if in_set is not None:
        add_row("Internal setpoint (IN_SET)", f"{in_set:.2f}", "°C")

    # ---- Compressor / pump ----
    cmp_req = _hex_to_int(kv.get("CMP_REQ", ""))
    if cmp_req is not None:
        add_row("Compressor requested (CMP_REQ)", "ON" if cmp_req != 0 else "OFF")

    w_pump = _hex_to_int(kv.get("W_PUMP", ""))
    if w_pump is not None:
        add_row("Water pump (W_PUMP)", "ON" if w_pump != 0 else "OFF")

    # ---- Operating / diagnostics (from ER_CODE table; now correctly aligned) ----
    o_status = _hex_to_int(kv.get("O_STATUS", ""))
    if o_status is not None:
        add_row("Operating status (O_STATUS)", f"0x{o_status:02X}")

    o_pipe = _hex_to_int(kv.get("O_PIPE", ""))
    if o_pipe is not None:
        add_row("Pipe state (O_PIPE)", f"0x{o_pipe:02X}")

    o_cur = _hex_to_int(kv.get("O_CUR", ""))
    if o_cur is not None:
        add_row("Compressor current (O_CUR)", str(o_cur), "raw")

    o_disc = _hex_to_int(kv.get("O_DISC", ""))
    if o_disc is not None:
        add_row("Discharge (O_DISC)", str(o_disc), "raw")

    o_val = _hex_to_int(kv.get("O_VAL", ""))
    if o_val is not None:
        add_row("Expansion valve (O_VAL)", str(o_val), "steps")

    comphz = _hex_to_int(kv.get("COMPHZ", ""))
    if comphz is not None:
        add_row("Compressor frequency (COMPHZ)", str(comphz), "Hz")

    hpower = _hex_to_int(kv.get("HPOWER", ""))
    if hpower is not None:
        add_row("Electrical power (HPOWER)", str(hpower), "W")
        add_row("Electrical power", f"{hpower/1000.0:.3f}", "kW")

    cpower = _hex_to_int(kv.get("CPOWER", ""))
    if cpower is not None:
        add_row("Cooling power (CPOWER)", str(cpower), "W")

    tpower = _hex_to_int(kv.get("TPOWER", ""))
    if tpower is not None:
        add_row("Total power (TPOWER)", str(tpower), "W")

    # Error code (already joined as "AA BB" by parser)
    if "ER_CODE" in kv:
        add_row("Error code (ER_CODE)", kv["ER_CODE"], "")

    if not rows:
        return

    name_w = max(len(r[0]) for r in rows)
    val_w = max(len(r[1]) for r in rows)

    print("[HUMAN VALUES]")
    print("-" * (name_w + val_w + 10))
    for name, val, unit in rows:
        unit_str = f" {unit}" if unit else ""
        print(f"{name:<{name_w}}  {val:>{val_w}}{unit_str}")
    print("-" * (name_w + val_w + 10))


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

            if len(stream_buf) > max_chars:
                stream_buf = stream_buf[-max_chars:]

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
