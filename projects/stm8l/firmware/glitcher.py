# Runs on the Pico (MicroPython)
import sys, ustruct, time

class Glitcher():
    def main(self):
        for _ in range(1000):
            time.sleep(0.1)
            sys.stdout.write(b"OK")
            sys.stdout.flush()
            continue
            hdr = sys.stdin.read(3)
            if not hdr or len(hdr) < 3:
                continue
            cmd = hdr[0]
            ln  = hdr[1] | (hdr[2] << 8)
            payload = sys.stdin.read(ln) if ln else b""
            # respond (example): echo "OK"
            sys.stdout.write(bytes((cmd,)))
            sys.stdout.write(ustruct.pack("<H", 2))
            sys.stdout.write(b"OK")
            sys.stdout.flush()
