#!/usr/bin/env python3

import argparse
import os
from projects.stm8l.firmware.glitcher import GlitcherClient
import random
import sys
import time
from dotenv import load_dotenv

from findus import Database, PicoGlitcher
from projects.stm8l.utils.pushover import send_pushover_notification
from ..utils.psu import PS3005D

RX_PIN = 27


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
    

""" 
check_empty bypass:
1. flash check_empty.ihx
2. flash bor_on.bin opt

rdp_check bypass:
1. flash empty 
2. flash rop_on.bin opt
"""

class Main:
    def __init__(self, args):
        self.args = args
        self.parameters = {
            "s_length": 900,
            "e_length": 1100,
            # "s_delay": 29450,
            # "e_delay": 29600,
            "s_delay": 28000,
            "e_delay": 30000,
            # "s_delay": 34000,
            # "e_delay": 38000,
            "voltage": 2.34,
        }
# 
        self.glitcher = GlitcherClient(args.rpico)
        self.glitcher.power_cycle_reset(50_000)
        self.glitcher.trigger_on_reset_pin()
        self.findus_glitcher = BootloaderProfilingGlitcher()

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

        self.psu.set_voltage(self.parameters["voltage"])
        time.sleep(0.1)
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        while True:
            delay = int(
                random.randint(self.parameters["s_delay"], self.parameters["e_delay"])
            )
            delay = round(delay / 4) * 4  # ensure delay is multiple of 4
            length = int(
                random.randint(self.parameters["s_length"], self.parameters["e_length"])
            )
            length = round(length / 4) * 4  # ensure length is multiple of

            self.glitcher.arm_double_multiplexing(delay, length, "VI1", delay + length + 100, length, "3.3")
            self.glitcher.reset(50)
            success = False

            try:
                self.glitcher.wait_done(0.1)
                raw_adc = self.glitcher.adc27()
                success = raw_adc > 500
                
                if success:
                    state = b"success"

                    send_pushover_notification(
                        message=f"Successful glitch! with delays={delay} ns, length={length} ns",
                        title="Successful glitch",
                    )
                else:
                    state = b"expected"
            except TimeoutError:
                print("[-] Timeout received in block(). Continuing.")
                self.glitcher.power_cycle_reset(20_000)
                state = b"timeout"

            color = self.findus_glitcher.classify(state)
            if state != b"expected":
                self.db.insert(
                    exp_id, self.parameters["voltage"] * 100, delay, length, color, state, commit=False
                )
    
            if exp_id % 10000:
                self.db.con.commit()
                
            speed = self.findus_glitcher.get_speed(self.start_time, exp_id)
            experiment_base_id = self.db.get_base_experiments_count()
            print(
                self.findus_glitcher.colorize(
                    f"[+] Experiment {exp_id}\t{experiment_base_id}\t({speed})\t{self.parameters['voltage']:.2f}\t{delay:>{len(str(self.parameters['e_delay']))}}\t{length}\t{color}\t{state}",
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
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
