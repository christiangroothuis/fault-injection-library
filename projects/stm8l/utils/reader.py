import serial
import time


class STM8Reader:
    ACK = 0x79
    NACK = 0x1F
    SYNCH = 0x7F

    def __init__(self, port, baud=115200, timeout=1.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser = None

    def enter_bootloader(self, tries=2):
        """
        Open port in 8E1, send 0x7F, expect ACK (0x79). Raises on failure.
        """
        self._open_8e1()
        time.sleep(0.003)
        self.ser.reset_input_buffer()
        for _ in range(tries):
            self._write(bytes([self.SYNCH]))
            a = self._read_exact(1, overall_timeout=0.8)
            if a == bytes([self.ACK]):
                time.sleep(0.002)
                self.ser.reset_input_buffer()
                return True
            time.sleep(0.05)
        raise RuntimeError("Bootloader sync failed (no ACK after 0x7F)")

    def read_memory(self, start_addr, length):
        """
        Read 'length' bytes starting at 'start_addr' using 0x11 (Read Memory).
        Returns bytes. Raises RuntimeError on NACK/timeout.
        """
        if not self.ser or not self.ser.is_open:
            self._open_8e1()

        data = bytearray()
        off = 0
        while off < length:
            n = min(256, length - off)
            blk = self._read_block(start_addr + off, n)
            if blk is None:
                raise RuntimeError("Read failed (NACK/timeout)")
            data += blk
            off += n
        return bytes(data)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def _open_8e1(self):
        if self.ser:
            self.close()
        self.ser = serial.Serial(
            self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=1.0,
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

    def _read_block(self, addr, count):
        time.sleep(0.002)
        self.ser.reset_input_buffer()

        self._write(b"\x11\xee")
        if not self._expect_ack():
            time.sleep(0.01)
            self.ser.reset_input_buffer()
            self._write(b"\x11\xee")
            if not self._expect_ack():
                return None

        a3 = (addr >> 24) & 0xFF
        a2 = (addr >> 16) & 0xFF
        a1 = (addr >> 8) & 0xFF
        a0 = addr & 0xFF

        self._write(bytes([a3, a2, a1, a0, (a3 ^ a2 ^ a1 ^ a0) & 0xFF]))

        if not self._expect_ack():
            return None

        n1 = (count - 1) & 0xFF
        self._write(bytes([n1, (0xFF - n1) & 0xFF]))
        if not self._expect_ack():
            return None

        return self._read_exact(count, overall_timeout=2.0)
