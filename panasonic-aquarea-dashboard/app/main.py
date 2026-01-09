# /app/main.py
#
# Panasonic Aquarea TCP dashboard reader (10.80.30.70:23 etc.)
# - Reads ANSI "status screen" stream over TCP
# - Detects full screens by clear-screen boundaries (ESC[...J)
# - Prints raw dashboard
# - Prints an additional "HUMAN VALUES" table with proper decoding
#
# Fix included:
# - Temperature line is NOT a simple header/value zip. It contains extra status bytes.
#   We now parse the first value line as:
#     R_MODE MODE  (word,status)x5  OUT(raw,status,disp)
#   This fixes wrong temps like 0.46°C that came from mistakenly decoding status bytes.

import os
import re
import sys
import time
import json
import socket
from typing import Dict, List, Tuple, Optional

# ANSI CSI sequences like ESC[2J, ESC[0K, ESC[H, etc.
ANSI_CSI_RE = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")

# "Clear screen" CSI ... J (ESC[2J, ESC[J, etc.)
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
    sock.settimeout(None)  # blocking recv
    return sock


def looks_like_dashboard(text: str) -> bool:
    # Your full screens contain at least these headings.
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
    NOTE: The first temperature line is NOT parsed correctly by this method (it has extra status bytes),
    so we parse that separately in extract_temp_line().
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
    Parses the first dashboard value line:
      R_MODE MODE (word status)* for each *_TMP_AD, and OUT_TEMP as (raw status disp)

    Expected token layout on the values line:
      R_MODE MODE
      WATER_TMP_WORD WATER_TMP_STATUS
      WATER2_TMP_WORD WATER2_TMP_STATUS
      IN_TMP_WORD IN_TMP_STATUS
      R1_TMP_WORD R1_TMP_STATUS
      TANK_TMP_WORD TANK_TMP_STATUS
      OUT_RAW OUT_STATUS OUT_DISP

    Total tokens: 2 + (5*2) + 3 = 15
    """
    lines = [ln.strip() for ln in clean_screen_text.split("\n") if ln.strip()]
    for idx, ln in enumerate(lines):
        if ln.startswith("R_MODE") and "OUT_TEMP" in ln:
            if idx + 1 >= len(lines):
                return None
            vals = lines[idx + 1].split()
            if len(vals) < 15:
                return None

            r_mode = vals[0]
            mode = vals[1]
            t = vals[2:]

            water_word, water_status = t[0], t[1]
            water2_word, water2_status = t[2], t[3]
            in_word, in_status = t[4], t[5]
            r1_word, r1_status = t[6], t[7]
            tank_word, tank_status = t[8], t[9]

            out_raw, out_status, out_disp = t[10], t[11], t[12]

            return {
                "r_mode": r_mode,
                "mode": mode,
                "water_word": water_word,
                "water_status": water_status,
                "water2_word": water2_word,
                "water2_status": water2_status,
                "in_word": in_word,
                "in_status": in_status,
                "r1_word": r1_word,
                "r1_status": r1_status,
                "tank_word": tank_word,
                "tank_status": tank_status,
                "out_raw": out_raw,
                "out_status": out_status,
                "out_disp": out_disp,
            }
    return None


def extract_er_code_pair(clean_screen_text: str) -> Optional[Tuple[str, str]]:
    """
    ER_CODE appears as two tokens on the ER_CODE row.
    Returns (major, minor) as hex strings.
    """
    lines = [ln.strip() for ln in clean_screen_text.split("\n") if ln.strip()]
    for idx, ln in enumerate(lines):
        if ln.startswith("ER_CODE") and "TPOWER" in ln:
            if idx + 1 < len(lines):
                vals = lines[idx + 1].split()
                if len(vals) >= 2:
                    a, b = vals[0], vals[1]
                    if _hex_to_int(a) is not None and _hex_to_int(b) is not None:
                        return a, b
    return None


def print_human_table(clean_screen_text: str) -> None:
    kv = parse_screen_tables(clean_screen_text)
    rows: List[Tuple[str, str, str]] = []

    def add_row(name: str, val: str, unit: str = ""):
        rows.append((name, val, unit))

    def add_temp(label: str, word_hex: str, status_hex: str):
        t = q88_temp_c(word_hex)
        if t is not None:
            add_row(label, f"{t:.2f}", "°C")
        add_row(label + " status", f"0x{status_hex.upper()}", "")

    # ---- Temperatures: parse from first line with (word,status) pairs ----
    temp = extract_temp_line(clean_screen_text)
    if temp:
        add_temp("Flow temp (WATER_TMP)", temp["water_word"], temp["water_status"])
        add_temp("Return temp (WATER2_TMP)", temp["water2_word"], temp["water2_status"])
        add_temp("Indoor/internal temp (IN_TMP)", temp["in_word"], temp["in_status"])
        add_temp("Zone/loop temp (R1_TMP)", temp["r1_word"], temp["r1_status"])
        add_temp("Tank temp (TANK_TMP)", temp["tank_word"], temp["tank_status"])

        # Outdoor: use rounded display byte when raw is invalid (status FF)
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

    # ---- Compressor frequency & power ----
    comphz = _hex_to_int(kv.get("COMPHZ", ""))
    if comphz is not None:
        add_row("Compressor frequency (COMPHZ)", str(comphz), "Hz")

    hpower = _hex_to_int(kv.get("HPOWER", ""))
    if hpower is not None:
        add_row("Electrical power (HPOWER)", str(hpower), "W")

    # ---- Mode flags ----
    hot = _hex_to_int(kv.get("HOT", ""))
    if hot is not None:
        add_row("HOT state", f"0x{hot:02X}")

    cool = _hex_to_int(kv.get("COOL", ""))
    if cool is not None:
        add_row("COOL state", f"0x{cool:02X}")

    freeze = _hex_to_int(kv.get("FREEZE", ""))
    if freeze is not None:
        add_row("FREEZE state", f"0x{freeze:02X}")

    # ---- Errors ----
    er = extract_er_code_pair(clean_screen_text)
    if er:
        a, b = er
        add_row("Error code (ER_CODE)", f"{a.upper()} {b.upper()}")

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
                # No clear seen yet; prevent runaway
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
