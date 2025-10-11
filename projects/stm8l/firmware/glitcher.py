import serial, struct, time
import subprocess

SOF_REQ = 0xAA
SOF_RESP = 0x55

CMD_ARM2 = 0x03
CMD_RESET = 0x04
CMD_PWRRST = 0x05
CMD_CHECK = 0x06
CMD_PINSTAT = 0x07
CMD_ADC27 = 0x08
CMD_TRIGGER_ON_RESET = 0x09
CMD_TRIGGER_ON_TRIGGER = 0x0A

ST_OK, ST_BAD_ARGS, ST_BUSY, ST_UNKNOWN = 0x00, 0x01, 0x02, 0x03

voltage_map = {
    "VI1": 0b00,
    "VI2": 0b10,
    "1.8": 0b01,
    "GND": 0b11,
    "3.3": 0b10,
    "VCC": 0b00,
}


class GlitcherError(RuntimeError):
    def __init__(self, cmd, status, msg=""):
        super().__init__(
            f"Glitcher status {status:#02x} on cmd {cmd:#02x}"
            + (f": {msg}" if msg else "")
        )
        self.cmd = cmd
        self.status = status


class GlitcherClient:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: serial.Serial | None = None
        
        self.reboot()
        self.open()

    def open(self):
        if self.ser and self.ser.is_open:
            return self
        self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        return self

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def reboot(self):
        subprocess.run(["picotool", "reboot", "-f"], check=True)
        time.sleep(0.1)
        self.open()
            
    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()

    def _read_exact(self, n: int) -> bytes:
        assert self.ser and self.ser.is_open
        out = bytearray()
        deadline = time.monotonic() + self.timeout
        while len(out) < n:
            if time.monotonic() > deadline:
                break
            chunk = self.ser.read(n - len(out))
            if chunk:
                out.extend(chunk)
        if len(out) != n:
            raise TimeoutError(f"Serial timeout waiting for {n} bytes (got {len(out)})")
        return bytes(out)

    def _recv_resp(self, expect_cmd: int) -> tuple[int, bytes]:
        """Returns (status, payload). Resyncs to 0x55 if needed."""
        assert self.ser and self.ser.is_open
        # Resync: read until 0x55
        deadline = time.monotonic() + self.timeout
        while True:
            b = self.ser.read(1)
            if b == bytes([SOF_RESP]):
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Serial timeout waiting for response SOF 0x55")
        hdr = self._read_exact(3)  # cmd, status, len
        cmd, status, length = hdr[0], hdr[1], hdr[2]
        payload = self._read_exact(length) if length else b""
        if cmd != expect_cmd:
            raise GlitcherError(
                cmd, status, f"mismatched resp (expected {expect_cmd:#02x})"
            )
        if status != ST_OK:
            raise GlitcherError(cmd, status)
        return status, payload

    def _send(self, cmd: int, payload: bytes = b"") -> bytes:
        assert self.ser and self.ser.is_open
        if len(payload) > 255:
            raise ValueError("payload too long")
        pkt = bytes([SOF_REQ, cmd, len(payload)]) + payload
        self.ser.write(pkt)
        _, pl = self._recv_resp(cmd)
        return pl

    def arm_double_multiplexing(
        self, d1_ns: int, len1_ns: int, v1: str, d2_ns: int, len2_ns: int, v2: str
    ):
        b = struct.pack(
            "<HHHHBB",
            int(d1_ns),
            int(len1_ns),
            int(d2_ns),
            int(len2_ns),
            voltage_map[v1.upper()],
            voltage_map[v2.upper()],
        )
        self._send(CMD_ARM2, b)

    def reset(self, reset_us: int):
        self._send(CMD_RESET, struct.pack("<H", int(reset_us)))

    def power_cycle_reset(self, power_cycle_us: int):
        self._send(CMD_PWRRST, struct.pack("<H", int(power_cycle_us)))

    def check_glitch(self) -> bool:
        pl = self._send(CMD_CHECK)
        return bool(pl[0]) if pl else False

    def wait_done(self, timeout_s: float) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if self.check_glitch():
                return
        raise TimeoutError("wait_done timeout")

    def pinstat(self) -> dict:
        pl = self._send(CMD_PINSTAT)
        m = pl[0]
        return {
            "success": (m & 0x01) != 0,
            "bor": (m & 0x02) != 0,
            "por": (m & 0x04) != 0,
            "trigger": (m & 0x08) != 0,
        }

    def adc27(self) -> tuple[int, int]:
        pl = self._send(CMD_ADC27)
        raw = struct.unpack("<H", pl)
        return raw[0]
    
    def trigger_on_reset_pin(self):
        self._send(CMD_TRIGGER_ON_RESET)

    def trigger_on_trigger_pin(self):
        self._send(CMD_TRIGGER_ON_TRIGGER)


# testing code
def find_ports_mac():
    """Returns a list of likely Pico serial ports on macOS."""
    import glob

    return sorted(glob.glob("/dev/tty.usbmodem*") + glob.glob("/dev/tty.usbserial*"))


if __name__ == "__main__":
    ports = find_ports_mac()
    if not ports:
        print("No serial ports found. Plug the board and check /dev/tty.usbmodem*")
        raise SystemExit(1)
    port = ports[0]
    # print(f"Using {port}")
    with GlitcherClient(port, timeout=2) as g:
        start = time.monotonic()
        g.arm_double_multiplexing(500, 20, "VI1", 600, 20, "VI1")  # example
        time.sleep(0.1)
        g.reset(10)
        done = g.wait_done(100e-6)
        print("Done:", done)
        print(g.pinstat().values())
        print("ADC27:", g.adc27())
        # g.power_cycle_reset(50)
        print(f"Elapsed: {time.monotonic() - start:.5f} s")
