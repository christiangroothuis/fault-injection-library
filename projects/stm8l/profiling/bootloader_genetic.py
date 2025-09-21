#!/usr/bin/env python3

import argparse
import os
import random
import sys
import time
from dotenv import load_dotenv

from findus import Database, PicoGlitcher, OptimizationController
from projects.stm8l.utils.pushover import send_pushover_notification
from ..utils.psu import PS3005D

RX_PIN = 27


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
        weight = 0
        if b"expected" in state:
            color = "G"
        elif b"timeout" in state:
            color = "Y"
        elif b"success" in state:
            color = "R"
            weight = 2
        return color, weight


class Main:
    def __init__(self, args):
        self.args = args
        self.parameters = {
            "s_length": 24,
            "e_length": 24,
            "s_delay": 25000,
            "e_delay": 35000,
            "voltage": 1.10,
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
            column_names=["voltage", "delay", "length"],
        )
        self.start_time = int(time.time())
        self.psu = PS3005D(port=args.psu)

        boundaries = [(self.parameters["s_delay"], self.parameters["e_delay"])]
        self.opt = OptimizationController(
            parameter_boundaries=boundaries,
            parameter_divisions=[30],
            number_of_individuals=20,
            length_of_genom=20,
            malus_factor_for_equal_bins=1,
        )

    def run(self):
        exp_id = 0

        self.psu.set_voltage(self.parameters["voltage"])
        time.sleep(0.1)
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        while True:
            if exp_id % 10000 == 0:
                self.opt.print_best_performing_bins()
                
            delay = int(random.randint(self.parameters["s_delay"], self.parameters["e_delay"]))
            delay = round(delay / 4) * 4 # ensure delay is multiple of 4
            length = int(random.randint(self.parameters["s_length"], self.parameters["e_length"]))
            length = round(length / 4) * 4

            mul_config = {"t1": length, "v1": "VI1"}
            self.glitcher.arm_multiplexing(delay, mul_config)
            self.glitcher.reset(100e-6)  # reset for 100us
            success = False

            try:
                self.glitcher.block(timeout=1)
                time.sleep(100e-6) # wait for rx to go high if success
                success = self.glitcher.read_success_flag()

                if success:
                    state = b"success"
                else:
                    state = b"expected"
            except:
                print("[-] Timeout received in block(). Continuing.")
                self.glitcher.power_cycle_reset(0.2)
                time.sleep(0.2)
                state = b"timeout"

            color, weight = self.glitcher.classify(state)
            self.opt.add_experiment(weight, delay)
            self.db.insert(exp_id, self.parameters["voltage"] * 100, delay, length, color, state)
            speed = self.glitcher.get_speed(self.start_time, exp_id)
            experiment_base_id = self.db.get_base_experiments_count()
            print(
                self.glitcher.colorize(
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
