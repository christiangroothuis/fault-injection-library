#!/usr/bin/env python3
import argparse
import time
from findus import PicoGlitcher, pyboard

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="STM8L single-glitch via external trigger + success pin"
    )
    p.add_argument(
        "--rpico",
        type=str,
        default="/dev/tty.usbmodem213301",
        help="Path to the Raspberry Pi Pico serial port",
    )
    args = p.parse_args()

    pg = PicoGlitcher()
    pg.init(port=args.rpico, enable_vtarget=False)

    while True:
        val_b = pg.pico_glitcher.pyb.exec(
            "from machine import ADC; print(ADC(27).read_u16())\n"
        )
        print(int(val_b.strip()) > 8000)

        continue
        # Connect to Pico
        pyb = pyboard.Pyboard(args.rpico)
        pyb.enter_raw_repl()

        pyb.exec("from machine import ADC")
        pyb.exec("adc = ADC(27)")

        while True:
            val_b = pyb.exec("from machine import ADC; print(ADC(27).read_u16())\n")
            val = int(val_b.strip())
            print(val)
            time.sleep(0.1)
