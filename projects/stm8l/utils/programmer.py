import os
import subprocess
import tempfile
from typing import Iterable, List
import argparse

DEFAULT_ERASE_PART = "stm8l151?4"
DEFAULT_PART = "stm8l051f3"
DEFAULT_TRANSPORT = "stlinkv2"
OPT_BASE_ADDR = 0x4800
OPT_BLOCK_BYTES = 16
CHIP_VERSION_ADDR = 0x483E
CHECK_EMPTY_FIRMWARE = "projects/stm8l/profiling/firmware/build/check_empty.ihx"
EMPTY_FIRMWARE = "projects/stm8l/profiling/firmware/empty_flash.bin"

RDP_OFF = bytes([0xAA] + [0x00] * 15)
BOR_ON = bytes([0x00] * 0x0A + [0x01] + [0x00] * 5)
BL_ON = bytes([0x00] * 0x0B + [0x55] + [0x00] * 4)


class Stm8flashError(RuntimeError):
    pass


class STM8Programmer:
    def __init__(self, part: str = DEFAULT_PART, transport: str = DEFAULT_TRANSPORT):
        self.part = part
        self.transport = transport

    def _cmd(self, *args: str) -> List[str]:
        return ["stm8flash", "-c", self.transport, "-p", self.part] + list(args)

    def _run(self, *args: str) -> None:
        cmd = self._cmd(*args)
        try:
            subprocess.run(
                cmd, check=True, stdout=subprocess.PIPE
            )
        except FileNotFoundError:
            raise Stm8flashError("stm8flash not found on PATH")
        except subprocess.CalledProcessError as e:
            raise Stm8flashError(f"stm8flash failed: {e}")

    def _tempfile_write(self, data: bytes) -> str:
        f = tempfile.NamedTemporaryFile(delete=False)
        try:
            f.write(data)
            f.flush()
            return f.name
        finally:
            f.close()

    def write_firmware(self, file_path: str) -> None:
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        self._run("-w", file_path, "-s", "flash")

    def unlock_rop(self, part: str = DEFAULT_ERASE_PART) -> None:
        subprocess.run(
            ["stm8flash", "-c", self.transport, "-p", part, "-u"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def read(self, addr: int, length: int) -> bytes:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        try:
            self._run("-r", tmp.name, "-s", hex(addr), "-b", str(length))
            with open(tmp.name, "rb") as f:
                return f.read(length)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def write(self, addr: int, data: bytes) -> None:
        path = self._tempfile_write(data)
        try:
            self._run("-w", path, "-s", hex(addr), "-b", str(len(data)))
        finally:
            os.unlink(path)

    def verify(self, addr: int, expected: bytes) -> bool:
        return self.read(addr, len(expected)) == expected

    @staticmethod
    def or_blocks(blocks: Iterable[bytes]) -> bytes:
        comb = bytearray(OPT_BLOCK_BYTES)
        for b in blocks:
            if len(b) < OPT_BLOCK_BYTES:
                raise ValueError("Each option template must be 16 bytes")
            for i in range(OPT_BLOCK_BYTES):
                comb[i] |= b[i]
        return bytes(comb)

    def read_option_bytes(self) -> bytes:
        return self.read(OPT_BASE_ADDR, OPT_BLOCK_BYTES)

    def write_option_bytes(
        self, features: Iterable[bytes], preserve_current=False, verify=True
    ) -> bytes:
        base = (
            self.read_option_bytes()
            if preserve_current
            else bytes([0x00] * OPT_BLOCK_BYTES)
        )
        block = self.or_blocks([base, *features])
        self.write(OPT_BASE_ADDR, block)
        rb = self.read_option_bytes() if verify else block
        if verify and rb != block:
            raise Stm8flashError(
                f"Option-byte verify failed:\n wrote: {block.hex()}\n read : {rb.hex()}"
            )
        return rb

    def read_chip_id(self) -> int:
        version = self.read(CHIP_VERSION_ADDR, 1)[0]

        if version == 0x00:
            raise Stm8flashError("Chip not initialized")

        return version

    def write_chip_id(self, chip_id: int) -> None:
        if not (1 <= chip_id <= 0xFF):
            raise ValueError("Chip ID must be between 1 and 255")
        self.write(CHIP_VERSION_ADDR, bytes([chip_id]))

    def flash_check_empty(self) -> None:
        self.write_firmware(CHECK_EMPTY_FIRMWARE)

    def flash_empty(self) -> None:
        self.write_firmware(EMPTY_FIRMWARE)


if __name__ == "__main__":
    args = argparse.ArgumentParser(description="STM8L Programmer")
    args.add_argument("--id", required=True, type=int, default=6, help="Chip ID to write")
    args = args.parse_args()
    p = STM8Programmer()
    p.write_chip_id(args.id)
    print(f"Chip version: {p.read_chip_id()}")
