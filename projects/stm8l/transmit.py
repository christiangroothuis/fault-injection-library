# Requires: sudo apt install pigpio python3-pigpio python3-spidev
# Run pigpio daemon once: sudo pigpiod

import time
import spidev
import pigpio
from .leekoq import LeeKoq

# --------------------------
# User settings
# --------------------------
SPI_BUS = 0
SPI_DEV = 0
SPI_HZ  = 4000000

CC1101_GPIO_DATA = 23        # Pi GPIO wired to CC1101 GDO0 (DATA IN for async TX)
FREQ_HZ = 433_920_000        # RF center frequency
TICK_US = 200                # length of 1 "tick" in microseconds (adjust to match your target)
PREAMBLE_TICKS = 46
PREAMBLE_GAP_TICKS = 20
TAIL_GAP_TICKS = 75
TOTAL_BITS = 66              # 8 bytes + 2 LSBs of 9th

# --------------------------
# CC1101 helpers
# --------------------------
# CC1101 strobes
SRES  = 0x30  # Reset
SFSTXON = 0x31
SXOFF   = 0x32
SCAL    = 0x33
SRX     = 0x34
STX     = 0x35
SIDLE   = 0x36
SFTX    = 0x3B
SFRX    = 0x3A

def cc_write_reg(spi, addr, value):
    spi.xfer2([addr, value])

def cc_strobe(spi, strobe):
    spi.xfer2([strobe])

def freq_regs_from_hz(freq_hz, f_xtal=26_000_000):
    # FREQ = freq * 2^16 / f_xtal
    val = int(freq_hz * (1<<16) / f_xtal)
    return ( (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF )

def cc1101_init_async_ook(spi, freq_hz):
    # Reset
    cc_strobe(spi, SRES)
    time.sleep(0.01)

    # GDO0 = serial data (input in TX)
    IOCFG0 = 0x02
    cc_write_reg(spi, IOCFG0, 0x0B)

    # Async serial mode (PKT_FORMAT = 3)
    PKTCTRL0 = 0x08
    cc_write_reg(spi, PKTCTRL0, 0b00000011)

    # ASK/OOK
    MDMCFG2 = 0x12
    cc_write_reg(spi, MDMCFG2, 0x30)

    # --- PA path: use PATABLE[0] ---
    FREND0 = 0x22
    cc_write_reg(spi, FREND0, 0x10)        # << was 0x11; now PA index = 0

    # Set PA level in PATABLE[0]
    PATABLE = 0x3E
    cc_write_reg(spi, PATABLE, 0xC6)       # ~0 dBm typical; bump to 0xC2/0xC6 for more

    # Frequency
    FREQ2, FREQ1, FREQ0 = 0x0D, 0x0E, 0x0F
    f2, f1, f0 = freq_regs_from_hz(freq_hz)
    cc_write_reg(spi, FREQ2, f2)
    cc_write_reg(spi, FREQ1, f1)
    cc_write_reg(spi, FREQ0, f0)

    # Channel BW / DR (DR not used in async, but fine)
    MDMCFG4, MDMCFG3 = 0x10, 0x11
    cc_write_reg(spi, MDMCFG4, 0xCA)
    cc_write_reg(spi, MDMCFG3, 0x83)

    DEVIATN = 0x15
    cc_write_reg(spi, DEVIATN, 0x00)

    # Ensure autocal before TX (optional but helpful)
    MCSM0 = 0x18
    cc_write_reg(spi, MCSM0, 0x18)  # FS_AUTOCAL = 2 (from IDLE to TX/RX), PO_TIMEOUT default

    # IDLE and flush TX FIFO
    cc_strobe(spi, SIDLE)
    cc_strobe(spi, SFTX)
    # Do an explicit synth calibrate once
    cc_strobe(spi, SCAL)
    time.sleep(0.005)

def enter_tx(spi):
    cc_strobe(spi, STX)

def leave_idle(spi):
    cc_strobe(spi, SIDLE)

# --------------------------
# Waveform builder (your format)
# --------------------------
def build_pigpio_wave(pi, gpio, payload_bytes, bits=66, tick_us=200):
    """
    Encodes:
      - Preamble: 46 ticks of square wave toggling (start low)
      - Gap: 20 ticks low
      - Payload bits LSB-first:
          bit=0 -> high 4 ticks, low 2 ticks
          bit=1 -> high 2 ticks, low 4 ticks
      - Tail gap: 75 ticks low
    """
    pulses = []
    # Helper to append (level, duration_us)
    def add_level(level, ticks):
        if ticks <= 0:
            return
        pulses.append((level, ticks * tick_us))

    # Ensure line starts low
    pi.write(gpio, 0)

    # Preamble: toggle every tick for 46 ticks, starting low at tick 0
    level = 0
    for _ in range(PREAMBLE_TICKS):
        add_level(level, 1)
        level ^= 1  # toggle each tick

    # Preamble gap: force low
    level = 0
    add_level(0, PREAMBLE_GAP_TICKS)

    # Payload: LSB-first across bytes, 66 bits total
    bit_count = 0
    for b in payload_bytes:
        for i in range(8):
            if bit_count >= bits:
                break
            bit = (b >> i) & 1  # LSB first
            # print all bits on one line
            print(f"{bit}", end="")
            # High phase
            add_level(1, 4 if bit == 0 else 2)
            # Low phase
            add_level(0, 2 if bit == 0 else 4)
            bit_count += 1
        if bit_count >= bits:
            break

    # Tail gap
    add_level(0, TAIL_GAP_TICKS)

    # Convert to pigpio waves
    pigpio_pulses = []
    current = 0
    for level, dur in pulses:
        if level != current:
            pigpio_pulses.append(pigpio.pulse(1<<gpio if level else 0, 0 if level else 1<<gpio, 0))
            current = level
        pigpio_pulses.append(pigpio.pulse(0,0,int(dur)))

    pi.wave_add_generic(pigpio_pulses)
    wid = pi.wave_create()
    return wid

def build_packet(serial_id: int, counter: int, button: int) -> bytes:
    derived_key = LeeKoq.normalkeygen(serial_id, 0x4368096494059787)
    plaintext = (button << 24) | counter
    print(f"Plaintext: {plaintext:08X}")
    cipher = LeeKoq.encrypt(plaintext, derived_key)

    packet = bytearray()
    packet += cipher.to_bytes(4, 'little')
    packet += serial_id.to_bytes(3, 'little')
    packet += button.to_bytes(1, 'little')
    packet += 0x00.to_bytes(1, 'little')

    return packet

# --------------------------
# Main
# --------------------------
if __name__ == "__main__":
    # SPI/CC1101
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEV)
    spi.max_speed_hz = SPI_HZ
    spi.mode = 0

    cc1101_init_async_ook(spi, FREQ_HZ)

    # pigpio
    pi = pigpio.pi()
    assert pi.connected, "pigpio daemon not running?"
    pi.set_mode(CC1101_GPIO_DATA, pigpio.OUTPUT)
    pi.write(CC1101_GPIO_DATA, 0)

    # Enter TX state
    enter_tx(spi)
    time.sleep(0.005)  # small settle

    serial_id = 0x156260
    counter = 0x4022
    # counter = 0x2506
    button = 0x50

    payload = build_packet(serial_id, counter, button)

    print(f"Payload: {[hex(b) for b in payload]}")

    # Build and send the waveform
    wid = build_pigpio_wave(pi, CC1101_GPIO_DATA, payload, bits=TOTAL_BITS, tick_us=TICK_US)
    if wid >= 0:
        pi.wave_send_once(wid)
        while pi.wave_tx_busy():
            time.sleep(0.001)
        pi.wave_delete(wid)

    # Back to IDLE
    leave_idle(spi)
    pi.write(CC1101_GPIO_DATA, 0)
    pi.stop()
    spi.close()
