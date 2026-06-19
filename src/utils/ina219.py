try:
    import smbus
    SMBUS_AVAILABLE = True
except ImportError:
    SMBUS_AVAILABLE = False

_REG_CONFIG       = 0x00
_REG_SHUNTVOLTAGE = 0x01
_REG_BUSVOLTAGE   = 0x02
_REG_POWER        = 0x03
_REG_CURRENT      = 0x04
_REG_CALIBRATION  = 0x05


class INA219:
    def __init__(self, i2c_bus=1, addr=0x40):
        if not SMBUS_AVAILABLE:
            raise RuntimeError("smbus not available")
        self.bus = smbus.SMBus(i2c_bus)
        self.addr = addr
        self._cal_value = 0
        self._current_lsb = 0
        self._power_lsb = 0
        self._configure()

    def _read(self, address):
        data = self.bus.read_i2c_block_data(self.addr, address, 2)
        return (data[0] * 256) + data[1]

    def _write(self, address, data):
        temp = [(data & 0xFF00) >> 8, data & 0xFF]
        self.bus.write_i2c_block_data(self.addr, address, temp)

    def _configure(self):
        self._current_lsb = 0.1524
        self._cal_value = 26868
        self._power_lsb = 0.003048
        self._write(_REG_CALIBRATION, self._cal_value)

        # 16V range, Gain /2 (80mV), 12-bit 32-sample avg, continuous shunt+bus
        config = (0x00 << 13) | (0x01 << 11) | (0x0D << 7) | (0x0D << 3) | 0x07
        self._write(_REG_CONFIG, config)

    def getBusVoltage_V(self):
        self._write(_REG_CALIBRATION, self._cal_value)
        return (self._read(_REG_BUSVOLTAGE) >> 3) * 0.004

    def getCurrent_mA(self):
        value = self._read(_REG_CURRENT)
        if value > 32767:
            value -= 65535
        return value * self._current_lsb

    def getPower_W(self):
        self._write(_REG_CALIBRATION, self._cal_value)
        value = self._read(_REG_POWER)
        if value > 32767:
            value -= 65535
        return value * self._power_lsb

    def get_battery_percent(self):
        v = self.getBusVoltage_V()
        p = (v - 3.0) / 1.2 * 100.0
        return max(0.0, min(100.0, p))
