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
from .utils.psu import PS3005D, PSUTimeoutError
from .utils.reader import STM8UartReader, STM8SpiReader, SyncTimeoutError

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
        self.delay1 = list(itertools.chain.from_iterable([
            # range(28990, 29030), # chip 3 specific
            range(29400, 29750), # actually good and generic enough for profiling jreq glitch
        ]))

        self.delay2 = list(itertools.chain.from_iterable([
            # range(35216, 35284), # chip 3 specific
            # range(35740, 35780), # chip 3 specific

            # range(34700, 34900), # actually good and generic enough
            # range(35200, 35400), # actually good and generic enough
            range(33000, 38000), # this too wide, but just testing
        ]))
        self.n_glitches = args.n_glitches

        if self.args.programmer:
            self.programmer = STM8SpiReader()
            self.programmer.open_spi()
        else:
            self.programmer = None

        self.glitcher = GlitcherClient(args.rpico)
        self.glitcher.trigger_on_reset_pin()
        self.glitcher.power_cycle_reset(50_000)
        self.findus_glitcher = BootloaderProfilingGlitcher()

        self.db = Database(
            sys.argv,
            resume=args.resume,
            nostore=args.no_store,
            column_names=["voltage", "delay1", "delay2", "length"],
        )
        self.start_time = int(time.time())
        self.psu = PS3005D(port=args.psu)

    def run(self):
        exp_id = 0

        self.psu.set_voltage(self.args.voltage[0])
        time.sleep(0.1)
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        while True:
            voltage = round(random.uniform(self.args.voltage[0], self.args.voltage[1]), 2)
            print(f"Setting PSU voltage to {voltage:.2f} V")
            try:
                self.psu.set_voltage(voltage)
                time.sleep(0.1)
            except PSUTimeoutError:
                time.sleep(0.5)
                self.psu.close()
                self.psu = PS3005D(port=self.args.psu)
                self.psu.set_voltage(voltage)
                time.sleep(0.1)

            for _ in range(self.n_glitches):
                length = random.randint(
                    self.args.length[0], self.args.length[1]
                )
                delay1 = random.choice(self.delay1)
                delay2 = random.choice(self.delay2)
                length = round(length / 4) * 4  # ensure length is multiple of 4
                delay1 = round(delay1 / 4) * 4  # ensure delay is multiple of 4
                delay2 = round(delay2 / 4) * 4

                state = b"expected"

                self.glitcher.arm_double_multiplexing(delay1, length, "VI1", delay2, length, "VI1")
                self.glitcher.reset(50)

                try:
                    self.glitcher.wait_done(0.1)
                except TimeoutError:
                    print("[-] Timeout received in wait_done(). Continuing.")
                    self.glitcher.power_cycle_reset(20_000)
                    time.sleep(0.2)
                    state = b"timeout"

                success = self.glitcher.adc27() > 500
                    
                if success and self.programmer:
                    try:
                        time.sleep(0.01)
                        tries = self.programmer.enter_bootloader(tries=1)
                        flash = self.programmer.read_memory(0x8000, 0x2000)
                        eeprom = self.programmer.read_memory(0x1000, 0x00FF)

                        rand_str = secrets.token_hex(4)
                        flash_filename = f"flash-{rand_str}.bin"
                        eeprom_filename = f"eeprom-{rand_str}.bin"
                        pathlib.Path(flash_filename).write_bytes(flash)
                        pathlib.Path(eeprom_filename).write_bytes(eeprom)
                        state = b"success"

                        print(
                            f"[+] Written {flash_filename} and {eeprom_filename}"
                        )
                        send_pushover_notification(
                            message=f"Successful double glitch! with delays={delay1},{delay2} ns, length={length} ns, voltage={voltage:.2f} V (used {tries} tries to sync)",
                            title="Successful glitch",
                        )
                    except SyncTimeoutError:
                        state = b"sync_timeout"
                        print("[-] Sync timeout error :(")
                        send_pushover_notification(
                            message=f"delays={delay1},{delay2} ns, length={length} ns, voltage={voltage:.2f}",
                            title="Sync timeout",
                        )
                    except TimeoutError:
                        state = b"read_timeout"
                        print("[-] Read timeout error")

                color = self.findus_glitcher.classify(state)
                if success:
                    self.db.insert(
                        exp_id,
                        voltage * 100,
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
                        f"[+] Experiment {exp_id}\t{experiment_base_id}\t({speed})\t{voltage:.2f}\t{delay1}\t{delay2}\t{length}\t{color}\t{state}",
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
        help="STM8 bootloader programmer serial port",
    )
    p.add_argument("--resume", action="store_true", help="Resume previous database run")
    p.add_argument(
        "--no-store", action="store_true", help="Do not write results to the database"
    )
    p.add_argument("--ic", required=True, help="IC number")
    p.add_argument(
        "--voltage",
        type=float,
        nargs=2,
        default=[0, 2.8],
        help="Voltage range to use",
    )
    p.add_argument(
        "--length", nargs=2, help="length start and end", type=int, default=[0, 500]
    )
    p.add_argument(
        "--n-glitches", type=int, default=25000, help="Number of glitches to perform"
    )
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
