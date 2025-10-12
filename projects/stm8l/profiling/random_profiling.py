#!/usr/bin/env python3

import argparse
from projects.stm8l.firmware.glitcher import GlitcherClient
import random
import sys
import time
import numpy as np

from dotenv import load_dotenv

from findus import Database, PicoGlitcher
from ..utils.psu import PS3005D, PSUTimeoutError
from ..utils.pushover import send_pushover_notification


class ProfilingGlitcher(PicoGlitcher):
    def classify(self, state: bytes) -> str:
        color = "C"
        if b"expected" in state:
            color = "G"
        elif b"timeout" in state:
            color = "Y"
        elif b"success" in state:
            color = "R"
        elif b"bor_reset" in state:
            color = "B"
        elif b"por_reset" in state:
            color = "M"
        return color


class Main:
    def __init__(self, args):
        self.args = args
        self.parameters = {
            "s_length": args.length[0],
            "e_length": args.length[1],
            "s_delay": 1876,
            "e_delay": 1912,
            "s_voltage": args.voltage[0],
            "e_voltage": args.voltage[1],
            "voltage_step": 0.01,
            "n_glitches": 10000,
        }

        self.glitcher = GlitcherClient(args.rpico)
        self.glitcher.trigger_on_trigger_pin()
        self.glitcher.power_cycle_reset(50_000)
        self.findus_glitcher = ProfilingGlitcher()

        self.db = Database(
            sys.argv + [f"{k}={v}" for k, v in self.parameters.items()],
            resume=args.resume,
            nostore=args.no_store,
            column_names=["voltage", "delay", "length"],
        )
        self.start_time = int(time.time())
        self.psu = PS3005D(port=args.psu)

    def run(self):
        exp_id = 0

        self.psu.set_voltage(self.parameters["s_voltage"])
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        voltages = np.arange(
            self.parameters["s_voltage"],
            self.parameters["e_voltage"] + self.parameters["voltage_step"],
            self.parameters["voltage_step"],
        )

        while True:
            voltage = random.choice(voltages)
            print(f"Setting PSU voltage to {voltage:.2f} V")
            try:
                self.psu.set_voltage(voltage)
                time.sleep(0.1)
            except PSUTimeoutError:
                time.sleep(0.5)
                self.psu.close()
                self.psu = PS3005D(port=self.args.psu)
                self.psu.set_voltage(voltage)

            for _ in range(self.parameters["n_glitches"]):
                if voltage < 0.8:
                    max_length = 200
                else:
                    max_length = self.parameters["e_length"]
                length = random.randint(
                    self.parameters["s_length"], max_length
                )
                delay = random.randint(
                    self.parameters["s_delay"], self.parameters["e_delay"]
                )
                length = round(length / 4) * 4  # ensure length is multiple of 4
                delay = round(delay / 4) * 4  # ensure delay is multiple of 4

                self.glitcher.arm_double_multiplexing(
                    delay, length, "VI1", delay + length + 100, length, "3.3"
                )

                self.glitcher.reset(50)
                success = False

                try:
                    self.glitcher.wait_done(0.1)
                    pin_sum = 0
                    i = 0
                    while pin_sum == 0:
                        result = self.glitcher.pinstat()
                        pin_sum = sum(result.values())
                        i += 1
                    print(f"Pins stable after {i} reads")

                    success = result["success"]
                    bor_reset = result["bor"]
                    por_reset = result["por"]

                    if success:
                        state = b"success"

                        # send_pushover_notification(
                        #     message=f"Successful glitch! with delays={delay} ns, length={length} ns, voltage={voltage:.2f} V",
                        #     title="Successful glitch",
                        # )
                    elif bor_reset:
                        state = b"bor_reset"
                    elif por_reset:
                        state = b"por_reset"
                    else:
                        state = b"expected"
                except TimeoutError:
                    print("[-] Timeout received in block(). Continuing.")
                    self.glitcher.power_cycle_reset(20_000)
                    time.sleep(0.01)
                    state = b"timeout"

                color = self.findus_glitcher.classify(state)
                if state != b"expected" or exp_id == 0:
                    self.db.insert(
                        exp_id, voltage * 100, delay, length, color, state, commit=False
                    )

                if exp_id % 1000 == 0:
                    self.db.con.commit()

                speed = self.findus_glitcher.get_speed(self.start_time, exp_id)
                experiment_base_id = self.db.get_base_experiments_count()
                print(
                    self.findus_glitcher.colorize(
                        f"[+] Experiment {exp_id}\t{experiment_base_id}\t({speed})\t{voltage:.2f}\t{delay:>{len(str(self.parameters['e_delay']))}}\t{length}\t{color}\t{state}",
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
        default="/dev/ttyUSB1",
        required=True,
        help="PicoGlitcher serial port",
    )
    p.add_argument(
        "--psu", default="/dev/ttyUSB0", required=True, help="PSU serial port"
    )
    p.add_argument("--resume", action="store_true", help="Resume previous database run")
    p.add_argument(
        "--no-store", action="store_true", help="Do not write results to the database"
    )
    p.add_argument("--ic", required=True, help="IC number")
    p.add_argument(
        "--shape", choices=["clean", "ringing"], default="clean", help="Glitch shape"
    )
    p.add_argument(
        "--length", required=True, nargs=2, help="length start and end", type=int
    )
    p.add_argument(
        "--voltage", required=True, nargs=2, help="voltage start and end", type=float
    )
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
