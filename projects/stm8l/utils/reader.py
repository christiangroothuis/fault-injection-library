import subprocess
import serial
import time


def disable_tx():
    subprocess.run(["pinctrl", "set", "14", "ip", "pn"], check=True)


def enable_tx():
    subprocess.run(["pinctrl", "set", "14", "a0"], check=True)


class STM8Reader:
    ACK = 0x79
    SYNCH = 0x7F

    def __init__(self, port, baud=115200, timeout=1.0, guard_ms=10):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.guard_ms = guard_ms  # wait after pin mux / open
        self.ser = None

    # ---------- public ----------
    def enter_bootloader(self, tries=1):
        """Open 8E1, guard delay, flush, send a small 0x7F train; expect ACK (0x79)."""
        self._open_8e1()
        enable_tx()
        time.sleep(self.guard_ms / 1000.0)
        self.ser.reset_input_buffer()

        for _ in range(max(1, tries)):
            if self._sync_train():
                # settle and clear stray bytes before first command
                time.sleep(0.002)
                self.ser.reset_input_buffer()
                return True
            # re-open between tries in case driver buffered a stale frame
            self._open_8e1()
            time.sleep(self.guard_ms / 1000.0)
            self.ser.reset_input_buffer()

        raise RuntimeError("Bootloader sync failed (no ACK after 0x7F)")

    def read_memory(self, start_addr, length):
        """Read 'length' bytes from 'start_addr' using 0x11 (chunked ≤256)."""
        if not self.ser or not self.ser.is_open:
            self._open_8e1()
            time.sleep(self.guard_ms / 1000.0)

        out = bytearray()
        off = 0
        while off < length:
            n = min(256, length - off)
            blk = self._read_block(start_addr + off, n)
            if blk is None:
                raise RuntimeError("Read failed (NACK/timeout)")
            out += blk
            off += n
        return bytes(out)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None
        disable_tx()

    # ---------- internals ----------
    def _open_8e1(self):
        if self.ser:
            try:
                self.ser.close()
            except:
                pass
        self.ser = serial.Serial(
            self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=1.0,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def _write(self, b):
        self.ser.write(b)
        self.ser.flush()

    def _read_exact(self, n, overall_timeout=None):
        if overall_timeout is None:
            overall_timeout = self.timeout
        deadline = time.monotonic() + overall_timeout
        buf = bytearray()
        while len(buf) < n:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
                continue
            if time.monotonic() >= deadline:
                return None
        return bytes(buf)

    def _expect_ack(self, t=0.8):
        return self._read_exact(1, overall_timeout=t) == bytes([self.ACK])

    def _sync_train(self):
        """
        Send a short train of 0x7F with tiny gaps; accept first ACK.
        This is robust against a stray sampled edge right after muxing the pins.
        """
        self.ser.reset_input_buffer()
        for _ in range(6):  # up to 6 attempts in one train
            self._write(bytes([self.SYNCH]))
            a = self._read_exact(1, overall_timeout=0.25)
            if a == bytes([self.ACK]):
                return True
            time.sleep(0.003)  # small gap between SYNCH bytes
        return False

    def _read_block(self, addr, count):
        # tiny inter-command guard + flush (critical for first command)
        time.sleep(0.002)
        self.ser.reset_input_buffer()

        # 1) CMD + complement
        self._write(b"\x11\xee")
        if not self._expect_ack():
            # Retry once; often the very first command after sync is the picky one
            time.sleep(0.008)
            self.ser.reset_input_buffer()
            self._write(b"\x11\xee")
            if not self._expect_ack():
                return None

        # 2) 32-bit address (MSB..LSB) + XOR
        a3 = (addr >> 24) & 0xFF
        a2 = (addr >> 16) & 0xFF
        a1 = (addr >> 8) & 0xFF
        a0 = addr & 0xFF
        self._write(bytes([a3, a2, a1, a0, (a3 ^ a2 ^ a1 ^ a0) & 0xFF]))
        if not self._expect_ack():
            return None

        # 3) length (N-1) + complement
        n1 = (count - 1) & 0xFF
        self._write(bytes([n1, (0xFF - n1) & 0xFF]))
        if not self._expect_ack():
            return None

        # 4) data
        return self._read_exact(count, overall_timeout=2.0)
