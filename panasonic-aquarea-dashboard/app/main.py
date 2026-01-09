import os, re, sys, time, json, socket

ANSI_CSI_RE = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")

# Matches ANY "clear screen / clear display" CSI J command (ESC [ ... J)
CLEAR_RE = re.compile(r"\x1B\[[0-9;?]*J")

def load_options():
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

def connect(host, port, timeout):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((host, port))
    sock.settimeout(None)
    return sock

def print_screen(screen_text: str, ts_fmt: str, strip: bool):
    if strip:
        screen_text = strip_ansi(screen_text)
    screen_text = normalize_text(screen_text)
    if not screen_text:
        return

    timestamp = time.strftime(ts_fmt, time.localtime())
    print("\n" + "=" * 60)
    print(f"[DASHBOARD] {timestamp}")
    print(screen_text)
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

    print(f"[INFO] Panasonic Aquarea TCP dashboard reader")
    print(f"[INFO] Connecting to {host}:{port}")
    sys.stdout.flush()

    max_bytes = max_buffer_kb * 1024

    sock = None
    stream_buf = ""        # decoded text buffer (small-ish)
    current_screen = ""    # current screen being built

    while True:
        try:
            if sock is None:
                print("[INFO] Connecting...")
                sys.stdout.flush()
                sock = connect(host, port, sock_timeout)
                print("[INFO] Connected")
                sys.stdout.flush()

            data = sock.recv(4096)
            if not data:
                raise ConnectionError("Socket closed by peer")

            # Decode bytes -> text (latin-1 preserves bytes 0-255 without decode errors)
            chunk = data.decode("latin-1", errors="replace")
            stream_buf += chunk

            # Keep buffers bounded
            if len(stream_buf) > max_bytes:
                stream_buf = stream_buf[-max_bytes:]

            # Process clear-screen boundaries.
            # We split the stream on CLEAR commands; each CLEAR indicates "start of a new screen".
            parts = CLEAR_RE.split(stream_buf)

            # If we have at least 2 parts, that means we saw at least one CLEAR.
            # Everything before the last part is complete up to a boundary.
            if len(parts) >= 2:
                # Reconstruct segments corresponding to screen contents between clears.
                # Example: [segment_before_first_clear, segment_between_clears, ..., tail_after_last_clear]
                for seg in parts[:-1]:
                    # seg is content leading up to a CLEAR (or between clears)
                    current_screen += seg
                    # Print the completed screen (but only if it looks like a real dashboard)
                    # Simple sanity filter: must contain ER_CODE or R_MODE headings
                    if ("ER_CODE" in current_screen) or ("R_MODE" in current_screen):
                        print_screen(current_screen, ts_fmt, do_strip_ansi)
                    current_screen = ""  # reset for next screen

                # Keep only the tail after the last CLEAR in stream_buf
                stream_buf = parts[-1]

            else:
                # No CLEAR found yet — keep building current screen from stream_buf occasionally
                # but do NOT print yet
                if len(stream_buf) > max_bytes // 2:
                    # prevent unbounded growth if CLEAR never comes
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
            # reset partial state (prevents printing half-screens after reconnect)
            stream_buf = ""
            current_screen = ""
            time.sleep(2)

if __name__ == "__main__":
    main()
