#!/usr/bin/env python3

import argparse
import time 
import random
from projects.stm8l.profiling.profiling import PS3005D


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Test PSU control script")

    p.add_argument(
        "--port", default="/dev/ttyUSB0", required=True, help="Serial port for the PSU"
    )

    psu = PS3005D(port=p.parse_args().port)

    while True:
        psu.set_voltage(random.uniform(0.0, 5.0))
        psu.device.write("VSET1?".encode())
        time.sleep(0.1)
        print(psu.device.read_all())
        print(f"Voltage: {psu.get_voltage():.2f} V")