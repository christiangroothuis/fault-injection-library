#!/usr/bin/env python3

import argparse
import itertools
import pathlib
from projects.stm8l.firmware.glitcher import GlitcherClient
import random
import subprocess
import sys
import time
import secrets
from dotenv import load_dotenv
from findus import Database, PicoGlitcher

from projects.stm8l.utils.pushover import send_pushover_notification
from .utils.psu import PS3005D
from .utils.reader import STM8Reader

RX_PIN = 27


def disable_tx():
    subprocess.run(["pinctrl", "set", "14", "ip", "pn"], check=True)


def enable_tx():
    subprocess.run(["pinctrl", "set", "14", "a0"], check=True)


class BootloaderProfilingGlitcher(PicoGlitcher):
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
            self.programmer = STM8Reader(port=args.programmer)

        self.findus_glitcher = BootloaderProfilingGlitcher()
        self.glitcher = GlitcherClient(args.rpico)
        self.glitcher.open()

        self.glitcher.power_cycle_reset(1000)

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

            self.glitcher.arm_double_multiplexing(delay1, length, "VI1", delay2, length, "VI1")
            self.glitcher.reset(50)
            success = False

            try:
                self.glitcher.wait_done(0.1) # connect TRIGGER to RESET
                time.sleep(100e-6)
                success = self.glitcher.adc27() > 500

                if success:
                    if self.args.programmer:
                        enable_tx()
                        self.programmer.enter_bootloader()
                        flash = self.programmer.read_memory(0x8000, 0x2000)
                        eeprom = self.programmer.read_memory(0x1000, 0x00FF)
                        self.programmer.close()
                        disable_tx()

                        rand_str = secrets.token_hex(4)
                        flash_filename = f"flash-{rand_str}.bin"
                        eeprom_filename = f"eeprom-{rand_str}.bin"
                        pathlib.Path(flash_filename).write_bytes(flash)
                        pathlib.Path(eeprom_filename).write_bytes(eeprom)
                        print(
                            f"[+] Written {flash_filename} and {eeprom_filename}"
                        )

                    # send_pushover_notification(
                    #     message=f"Successful glitch! with delays={delay1},{delay2} ns, length={length} ns, voltage={self.parameters['voltage']:.2f} V",
                    #     title="Successful glitch",
                    # )

                    if self.args.programmer:
                        break

                    state = b"success"
                else:
                    state = b"expected"
            except TimeoutError:
                print("[-] Timeout received in block(). Continuing.")
                self.glitcher.power_cycle_reset(20_000)
                time.sleep(0.2)
                state = b"timeout"

            color = self.findus_glitcher.classify(state)
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
            speed = self.findus_glitcher.get_speed(self.start_time, exp_id)
            experiment_base_id = self.db.get_base_experiments_count()
            print(
                self.findus_glitcher.colorize(
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
