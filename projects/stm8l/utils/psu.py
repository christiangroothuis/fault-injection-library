from serial import Serial
import time

class PSUTimeoutError(Exception):
    pass

class PS3005D:
    def __init__(self, port):
        self.device = Serial(port=port, baudrate=9600)

    def get_voltage(self) -> float:
        """
        Gets the current output voltage of the psu

        Returns:
            float: The current output voltage, in volts (V).
        """
        self.device.write("VSET1?".encode())
        response = self.device.read(5).decode().strip()

        return float(response)

    def set_voltage(self, voltage: float, attempts: int = 10):
        """
        Sets the output voltage of the psu

        Args:
            voltage (float): The voltage to set, in volts (V).
        """
        while abs(self.get_voltage() - voltage) > 0.01:
            time.sleep(0.1)
            if attempts == 0:
                raise PSUTimeoutError("Failed to set voltage on PSU")
            self.device.write(f"VSET1:{voltage:05.2f}".encode())
            time.sleep(0.1)
            attempts -= 1

    def set_current_limit(self, current: float):
        """
        Sets the current limit of the psu

        Args:
            current (float): The current limit to set, in amperes (A).
        """
        self.device.write(f"ISET1:{current:.3f}".encode())

    def turn_on(self):
        self.device.write("OUT1".encode())

    def turn_off(self):
        self.device.write("OUT0".encode())

    def __del__(self):
        self.turn_off()
        self.device.close()
