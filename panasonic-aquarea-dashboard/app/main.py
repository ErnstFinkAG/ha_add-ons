import os
import re
import sys
import time
import json
import socket

ANSI_CSI_RE = re.compile(r"\x1B\[[0-9;?]*[A-Za-z]")

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
    sock.settimeout(None)  # blocking for recv
    return sock

def main():
    opt = load_options()

    host = opt.get("host", "10.80.30.70")
    port = int(opt.get("port", 23))
    print_interval = int(opt.get("print_interval_sec", 5))
    do_strip_ansi = bool(opt.get("strip_ansi", True))
    max_buffer_kb = int(opt.get("max_buffer_kb", 256))
    sock_timeout = int(opt.get("socket_timeout_sec", 1))

    print(f"[INFO] Panasonic Aquarea TCP dashboard reader")
    print(f"[INFO] Connecting to {host}:{port}")
    sys.stdout.flush()

    buf = bytearray()
    max_bytes = max_buffer_kb * 1024
    last_print = 0.0
    sock = None

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

            buf.extend(data)
            if len(buf) > max_bytes:
                buf = buf[-max_bytes:]

            now = time.time()
            if now - last_print >= print_interval:
                last_print = now

                text = buf.decode("latin-1", errors="replace")

                # Split on screen clears if present
                screens = re.split(r"\x1B\[[0-9;?]*J", text)
                tail = screens[-1] if screens else text

                if do_strip_ansi:
                    tail = strip_ansi(tail)

                tail = normalize_text(tail)

                if tail:
                    ts_fmt = opt.get("timestamp_format", "%Y-%m-%d %H:%M:%S")
                    timestamp = time.strftime(ts_fmt, time.localtime())

                    print("\n" + "=" * 60)
                    print(f"[DASHBOARD] {timestamp}")
                    print(tail)
                    print("=" * 60 + "\n")
                    sys.stdout.flush()


        except Exception as e:
            print(f"[WARN] Connection issue: {e}")
            sys.stdout.flush()
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
            sock = None
            time.sleep(2)  # backoff before reconnect

if __name__ == "__main__":
    main()
