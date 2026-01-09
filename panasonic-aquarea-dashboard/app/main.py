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
    # Your full screens contain at least R_MODE and/or ER_CODE headings.
    return ("R_MODE" in text) and ("ER_CODE" in text)


# ---------- decoding helpers ----------

def _hex_to_int(token: str) -> Optional[int]:
    token = token.strip()
    if not token:
        return None
    # tokens in your screen are hex without 0x
    # (but sometimes there can be non-hex garbage; ignore)
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
    Parses header/value tables from the screen.
    Returns a dict mapping FIELD_NAME -> token (hex string).
    If a field appears multiple times, last one wins.
    """
    lines = [ln.strip() for ln in screen_text.split("\n") if ln.strip()]
    out: Dict[str, str] = {}

    # A header line looks like: ER_CODE O_STATUS O_PIPE ...
    header_re = re.compile(r"^(?:[A-Z0-9_]+(?:\s+|$)){2,}$")

    i = 0
    while i < len(lines):
        header = lines[i]
        if header_re.match(header) and "DASHBOARD" not in header:
            headers = header.split()
            # Find next line with enough tokens
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


def decode_human(kv: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """
    Return rows: (name, value, unit)
    """
    rows: List[Tuple[str, str, str]] = []

    def add(name: str, value: Optional[float], unit: str, fmt: str = "{:.2f}"):
        if value is None:
            return
        rows.append((name, fmt.format(value), unit))

    def add_int(name: str, value: Optional[int], unit: str):
        if value is None:
            return
        rows.append((name, str(value), unit))

    def add_bool(name: str, token: Optional[str]):
        if token is None:
            return
        v = _hex_to_int(token)
        if v is None:
            return
        rows.append((name, "ON" if v != 0 else "OFF", ""))

    # Temperatures (Q8.8)
    add("Flow temp (WATER_TMP)", q88_temp_c(kv.get("WATER_TMP_AD", "")), "°C")
    add("Return temp (WATER2_TMP)", q88_temp_c(kv.get("WATER2_TMP_AD", "")), "°C")
    add("Indoor/internal temp (IN_TMP)", q88_temp_c(kv.get("IN_TMP_AD", "")), "°C")
    add("Zone/loop temp (R1_TMP)", q88_temp_c(kv.get("R1_TMP_AD", "")), "°C")
    add("Tank temp (TANK_TMP)", q88_temp_c(kv.get("TANK_TMP_AD", "")), "°C")

    # Setpoints
    # REMO_SET appears to be integer °C; IN_SET is Q8.8
    remo = _hex_to_int(kv.get("REMO_SET", ""))
    if remo is not None:
        rows.append(("Remote setpoint (REMO_SET)", f"{remo}", "°C"))
    add("Internal setpoint (IN_SET)", q88_temp_c(kv.get("IN_SET", "")), "°C")

    # Outdoor temp special: your screen shows OUT_TEMP as 3 tokens after it.
    # Our table parser only captures the first token (it maps OUT_TEMP -> "EC00").
    # So for OUT_TEMP we need to re-parse from the raw screen, but we can approximate:
    # We use these keys if they exist (future-proof if you later add explicit parsing),
    # otherwise we leave outdoor temp blank.
    #
    # Recommendation: we’ll derive it by scanning for the OUT_TEMP line pattern in the raw screen
    # in print_screen_with_human().
    #
    # Compressor/Pump
    add_bool("Compressor requested (CMP_REQ)", kv.get("CMP_REQ"))
    add_bool("Water pump (W_PUMP)", kv.get("W_PUMP"))

    # Compressor frequency and power
    comphz = _hex_to_int(kv.get("COMPHZ", ""))
    if comphz is not None:
        rows.append(("Compressor frequency (COMPHZ)", str(comphz), "Hz"))

    hpower = _hex_to_int(kv.get("HPOWER", ""))
    if hpower is not None:
        rows.append(("Electrical power (HPOWER)", str(hpower), "W"))

    # Modes
    hot = _hex_to_int(kv.get("HOT", ""))
    cool = _hex_to_int(kv.get("COOL", ""))
    freeze = _hex_to_int(kv.get("FREEZE", ""))
    if hot is not None:
        rows.append(("HOT state", f"0x{hot:02X}", ""))
    if cool is not None:
        rows.append(("COOL state", f"0x{cool:02X}", ""))
    if freeze is not None:
        rows.append(("FREEZE state", f"0x{freeze:02X}", ""))

    # Error code
    # ER_CODE is two bytes displayed as two tokens in the screen; our parser captures ER_CODE -> first token.
    # We'll leave detailed ER_CODE handling to the raw scan (same as OUT_TEMP) for correctness.

    return rows


def extract_outdoor_triplet(clean_screen_text: str) -> Optional[Tuple[str, str, str]]:
    """
    Finds the OUT_TEMP triplet that appears at end of the first data line:
    ... EC00 FF 03
    Returns (raw_word, status, display) as hex strings.
    """
    # Look for a line containing OUT_TEMP header then the next line with values.
    lines = [ln.strip() for ln in clean_screen_text.split("\n") if ln.strip()]
    for idx, ln in enumerate(lines):
        if ln.startswith("R_MODE") and "OUT_TEMP" in ln:
            # next non-empty line should be values
            if idx + 1 < len(lines):
                vals = lines[idx + 1].split()
                # We expect: R_MODE MODE (then 5 temps with status each = 10 tokens) then OUT raw status display (3 tokens)
                # Total expected tokens: 2 + 10 + 3 = 15
                if len(vals) >= 15:
                    raw_word = vals[-3]
                    status = vals[-2]
                    disp = vals[-1]
                    if _hex_to_int(raw_word) is not None and _hex_to_int(status) is not None and _hex_to_int(disp) is not None:
                        return raw_word, status, disp
    return None


def extract_er_code_pair(clean_screen_text: str) -> Optional[Tuple[str, str]]:
    """
    Finds ER_CODE two-token value from ER_CODE table.
    Returns (major, minor) as hex strings.
    """
    lines = [ln.strip() for ln in clean_screen_text.split("\n") if ln.strip()]
    for idx, ln in enumerate(lines):
        if ln.startswith("ER_CODE") and "TPOWER" in ln:
            # next non-empty line should be values
            if idx + 1 < len(lines):
                vals = lines[idx + 1].split()
                if len(vals) >= 2:
                    a, b = vals[0], vals[1]
                    if _hex_to_int(a) is not None and _hex_to_int(b) is not None:
                        return a, b
    return None


def print_human_table(clean_screen_text: str) -> None:
    kv = parse_screen_tables(clean_screen_text)
    rows = decode_human(kv)

    # OUT_TEMP triplet -> add derived outdoor temp and raw/status fields
    out_trip = extract_outdoor_triplet(clean_screen_text)
    if out_trip:
        raw_word, status, disp = out_trip
        rows.append(("Outdoor raw word", f"0x{raw_word.upper()}", ""))
        rows.append(("Outdoor raw status", f"0x{status.upper()}", ""))
        disp_i = _hex_to_int(disp)
        if disp_i is not None:
            rows.append(("Outdoor temperature (rounded)", str(disp_i), "°C"))

        # If someday status becomes valid (not FF), show decoded Q8.8 too
        st_i = _hex_to_int(status)
        if st_i is not None and st_i != 0xFF:
            t = q88_temp_c(raw_word)
            if t is not None:
                rows.append(("Outdoor temperature (raw decoded)", f"{t:.2f}", "°C"))

    # ER_CODE pair
    er = extract_er_code_pair(clean_screen_text)
    if er:
        a, b = er
        rows.append(("Error code (ER_CODE)", f"{a.upper()} {b.upper()}", ""))

    if not rows:
        return

    # Pretty print as a table
    name_w = max(len(r[0]) for r in rows)
    val_w = max(len(r[1]) for r in rows)
    print("[HUMAN VALUES]")
    print("-" * (name_w + val_w + 10))
    for name, val, unit in rows:
        unit_str = f" {unit}" if unit else ""
        print(f"{name:<{name_w}}  {val:>{val_w}}{unit_str}")
    print("-" * (name_w + val_w + 10))


def print_screen_with_human(screen_text: str, ts_fmt: str, do_strip: bool) -> None:
    # Always keep a clean version for parsing (ANSI stripped)
    clean = strip_ansi(screen_text)
    clean = normalize_text(clean)
    if not clean:
        return

    # Print the raw dashboard (optionally stripped) like before
    shown = clean if do_strip else normalize_text(screen_text)
    timestamp = time.strftime(ts_fmt, time.localtime())

    print("\n" + "=" * 60)
    print(f"[DASHBOARD] {timestamp}")
    print(shown)
    print()
    # Add human readable table
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

    max_chars = max_buffer_kb * 1024  # decoded text, count chars

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
                # No clear seen yet, avoid runaway
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
