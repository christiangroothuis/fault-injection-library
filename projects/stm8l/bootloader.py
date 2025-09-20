#!/usr/bin/env python3

import argparse
import os
import random
import sys
import time
from dotenv import load_dotenv

from findus import Database, PicoGlitcher
from projects.stm8l.utils.psu import PS3005D
from projects.stm8l.utils.pushover import send_pushover_notification

RX_PIN = 19


class BootloaderGlitcher(PicoGlitcher):
    def init(self, *args, **kwargs):
        super().init(*args, **kwargs)

        self.pico_glitcher.pyb.exec_raw_no_follow(
            "import machine\n" f"adc = machine.ADC(machine.Pin({RX_PIN}))\n"
        )

    def read_success(self) -> bool:
        # adc input should be around 9000 for success
        # its normally around 400-500
        out = self.pico_glitcher.pyb.exec(
            "print(adc.read_u16())\n"
        )
        return int(out[0].strip()) > 8000

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

        self.glitcher = BootloaderGlitcher()
        self.glitcher.init(port=args.rpico, enable_vtarget=False)
        self.glitcher.change_config_and_reset("mux_vinit", "3.3")
        self.glitcher.init(port=args.rpico, enable_vtarget=False)

        self.glitcher.rising_edge_trigger()
        self.glitcher.set_multiplexing()

        self.glitcher.power_cycle_reset(0.01)

        self.db = Database(
            sys.argv,
            resume=args.resume,
            nostore=args.no_store,
            column_names=["voltage", "delay", "length"],
        )
        self.start_time = int(time.time())

        self.psu = PS3005D(port=args.psu)

    def run(self):
        s_length = 4
        e_length = 200
        s_delay = 1850
        e_delay = 1900
        voltage = 1.39
        n_glitches = 500

        exp_id = 0

        self.psu.set_voltage(voltage)
        time.sleep(0.1)
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        for _ in range(n_glitches):
            length = random.randint(s_length, e_length)
            delay = random.randint(s_delay, e_delay)
            mul_config = {"t1": length, "v1": "VI1"}
            self.glitcher.arm_multiplexing(delay, mul_config)
            self.glitcher.reset(100e-6)  # reset for 100us
            success = False

            try:
                self.glitcher.block(timeout=1)
                time.sleep(60e-6)  # wait for USART_RX pin to go high

                success = self.glitcher.read_success()

                if success:
                    state = b"success"
                    send_pushover_notification(
                        user_key=os.getenv("PUSHOVER_USER_KEY"),
                        app_token=os.getenv("PUSHOVER_APP_TOKEN"),
                        message=f"Successful glitch! with delay={delay} ns, length={length} ns, voltage={voltage:.2f} V",
                        title="Successful glitch",
                    )
                else:
                    state = b"expected"
            except:
                print("[-] Timeout received in block(). Continuing.")
                self.glitcher.power_cycle_reset(0.2)
                time.sleep(0.2)
                state = b"timeout"

            color = self.glitcher.classify(state)
            if success:
                self.db.insert(exp_id, voltage * 100, delay, length, color, state)
            speed = self.glitcher.get_speed(self.start_time, exp_id)
            experiment_base_id = self.db.get_base_experiments_count()
            print(
                self.glitcher.colorize(
                    f"[+] Experiment {exp_id}\t{experiment_base_id}\t({speed})\t{voltage:.2f}\t{delay:>{len(str(e_delay))}}\t{length}\t{color}\t{state}",
                    color,
                )
            )
            exp_id += 1

        self.psu.turn_off()


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
    p.add_argument(
        "--reset-hold", type=float, default=0.01, help="Target reset hold time (s)"
    )
    p.add_argument(
        "--block-timeout",
        type=float,
        default=1.0,
        help="Timeout waiting for glitch (s)",
    )
    p.add_argument(
        "--trigger-input",
        default="default",
        help="The trigger input to use (default, alt, ext1, ext2). The inputs ext1 and ext2 require the PicoGlitcher v2.",
    )
    p.add_argument("--resume", action="store_true", help="Resume previous database run")
    p.add_argument(
        "--no-store", action="store_true", help="Do not write results to the database"
    )
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
