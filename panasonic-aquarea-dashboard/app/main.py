import os
import re
import sys
import time
import json
import socket

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
    # Very cheap sanity check so we don't print junk:
    # Your full screens contain at least R_MODE and/or ER_CODE headings.
    return ("R_MODE" in text) or ("ER_CODE" in text)


def print_screen(screen_text: str, ts_fmt: str, do_strip: bool) -> None:
    if do_strip:
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

    max_chars = max_buffer_kb * 1024  # we store decoded text, so count chars

    print("[INFO] Panasonic Aquarea TCP dashboard reader")
    print(f"[INFO] Connecting to {host}:{port}")
    sys.stdout.flush()

    sock = None

    # Buffers
    stream_buf = ""        # decoded stream, used for splitting on CLEAR
    current_screen = ""    # collects text between CLEAR boundaries

    # Sync flag: first "screen" after connect is often partial (mid-refresh).
    # We discard the first boundary-delimited screen, then print all subsequent ones.
    synced = False

    while True:
        try:
            if sock is None:
                print("[INFO] Connecting...")
                sys.stdout.flush()
                sock = connect(host, port, sock_timeout)
                print("[INFO] Connected")
                sys.stdout.flush()

                # Reset state on every (re)connect
                stream_buf = ""
                current_screen = ""
                synced = False

            data = sock.recv(4096)
            if not data:
                raise ConnectionError("Socket closed by peer")

            chunk = data.decode("latin-1", errors="replace")
            stream_buf += chunk

            # Bound memory
            if len(stream_buf) > max_chars:
                stream_buf = stream_buf[-max_chars:]

            # Split by clear-screen markers.
            parts = CLEAR_RE.split(stream_buf)

            # If we saw >=1 clear marker, we have complete segments to process.
            if len(parts) >= 2:
                # Process all complete segments up to the last tail
                for seg in parts[:-1]:
                    current_screen += seg

                    if looks_like_dashboard(current_screen):
                        if synced:
                            # Now aligned: print full screen
                            print_screen(current_screen, ts_fmt, do_strip_ansi)
                        else:
                            # First screen after connect likely partial -> discard, then sync
                            synced = True

                    # Reset for the next screen segment
                    current_screen = ""

                # Keep only the tail after the last CLEAR in the buffer
                stream_buf = parts[-1]

            else:
                # No CLEAR seen yet; keep buffering.
                # If sender never sends CLEAR, prevent runaway by shifting into current_screen occasionally.
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

            # Backoff
            time.sleep(2)


if __name__ == "__main__":
    main()
