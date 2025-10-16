import serial
import time
import spidev

class SyncTimeoutError(TimeoutError):
    pass
    

ACK = 0x79
NACK = 0x1F
SYNCH = 0x7F

class STM8UartReader:

    def __init__(self, port, baud=115200, timeout=1.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser = None

    def enter_bootloader(self, tries=2):
        if not self.ser or not self.ser.is_open:
            self.open_8e1()

        self.ser.reset_input_buffer()
        for _ in range(tries):
            self._write(bytes([SYNCH]))
            a = self._read_exact(1, overall_timeout=0.8)
            if a == bytes([ACK]):
                time.sleep(0.002)
                self.ser.reset_input_buffer()
                return True
            time.sleep(0.05)
        raise SyncTimeoutError("Bootloader sync failed (no ACK after 0x7F)")

    def read_memory(self, start_addr, length):
        if not self.ser or not self.ser.is_open:
            self.open_8e1()

        data = bytearray()
        offset = 0
        while offset < length:
            n = min(256, length - offset)
            blk = self._read_block(start_addr + offset, n)
            if blk is None:
                raise RuntimeError("Read failed (NACK/timeout)")
            data += blk
            offset += n
        return bytes(data)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def open_8e1(self):
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
        return self._read_exact(1, overall_timeout=t) == bytes([ACK])

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


class STM8SpiReader:
    TOKEN= 0x00
    TOK_DELAY = 6e-6

    def __init__(self, bus=0, device=0, max_hz=50_000, timeout=1.0):
        self.bus = bus
        self.device = device
        self.max_hz = max_hz
        self.timeout = timeout
        self.spi = None

    def enter_bootloader(self, tries=2):
        if not self.spi:
            self.open_spi()

        for i in range(tries):
            self._write_bytes([SYNCH])
            a = self._read_exact_spi(1, overall_timeout=0.8)

            if a and a[0] == ACK:
                return i

            time.sleep(0.05)
        raise SyncTimeoutError()

    def read_memory(self, start_addr, length):
        if not self.spi:
            self.open_spi()

        data = bytearray()
        offset = 0

        while offset < length:
            n = min(256, length - offset)
            blk = self._read_block(start_addr + offset, n)

            if blk is None:
                raise TimeoutError("Read failed (NACK/timeout)")

            data += blk
            offset += n

        return bytes(data)

    def close(self):
        if self.spi:
            try:
                self.spi.close()
            finally:
                self.spi = None

    def open_spi(self):
        self.spi = spidev.SpiDev()
        self.spi.open(self.bus, self.device)
        self.spi.mode = 0
        self.spi.max_speed_hz = self.max_hz 
        self.spi.lsbfirst = False

    def _write_bytes(self, b):
        self.spi.xfer2(list(b))

    def _read_exact_spi(self, n, overall_timeout=None):
        if overall_timeout is None:
            overall_timeout = self.timeout

        deadline = time.monotonic() + overall_timeout
        out = bytearray()

        while len(out) < n:
            if time.monotonic() >= deadline:
                return None

            time.sleep(self.TOK_DELAY)
            rx = self.spi.xfer2([self.TOKEN])
            out.append(rx[0])

        return bytes(out)

    def _expect_ack(self, t=0.8):
        b = self._read_exact_spi(1, overall_timeout=t)
        return (b is not None) and (b[0] == ACK)

    def _read_block(self, addr, count):
        self._write_bytes([0x11, 0xEE])
        if not self._expect_ack():
            time.sleep(0.01)
            self._write_bytes([0x11, 0xEE])
            if not self._expect_ack():
                return None

        a3 = (addr >> 24) & 0xFF
        a2 = (addr >> 16) & 0xFF
        a1 = (addr >> 8) & 0xFF
        a0 = addr & 0xFF

        self._write_bytes([a3, a2, a1, a0, (a3 ^ a2 ^ a1 ^ a0) & 0xFF])

        if not self._expect_ack():
            return None

        n1 = (count - 1) & 0xFF
        self._write_bytes([n1, (0xFF - n1) & 0xFF])

        if not self._expect_ack():
            return None

        out = bytearray()
        remaining = count

        while remaining:
            chunk = min(4096, remaining)
            time.sleep(self.TOK_DELAY)
            out += bytes(self.spi.xfer2([self.TOKEN] * chunk))
            remaining -= chunk
        return bytes(out)
