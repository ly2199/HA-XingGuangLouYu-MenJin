"""星光楼宇 FME3 门禁控制器 HA 自定义集成。"""
import logging
import threading
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import DOMAIN, PORT, BAUD, READ_TIMEOUT, parse_frame
from .const import CMD_CALL_START, CMD_ACK, CMD_HANGUP, CMD_ANSWER
from .const import MONITOR, UNLOCK34

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LOCK, Platform.BUTTON, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: dict):
    """YAML 入口: 创建导入条目交给 config_flow 处理."""
    if any(e.domain == DOMAIN for e in hass.config_entries.async_entries()):
        return True
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "import"}, data={}
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """UI/导入配置入口."""
    hass.data.setdefault(DOMAIN, {})
    if "bus" not in hass.data[DOMAIN]:
        bus = MenjinBus(hass)
        hass.data[DOMAIN]["bus"] = bus
        bus.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


class MenjinBus:
    """串口总线读写器 (后台线程)."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._thread: threading.Thread | None = None
        self._running = False
        self._ser = None
        self._lock = threading.Lock()
        self.last_event: dict | None = None
        self.call_active = False
        self.video_active = False
        self.unlocked = False
        self.unlock_time = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="MenjinBus")
        self._thread.start()

    def _open_serial(self):
        import serial
        try:
            self._ser = serial.Serial(PORT, BAUD, timeout=READ_TIMEOUT)
            _LOGGER.info("串口 %s 已打开", PORT)
        except Exception as e:
            _LOGGER.warning("串口打开失败 (将每秒重试): %s", e)
            self._ser = None

    def send(self, frame: bytes) -> bool:
        with self._lock:
            try:
                if self._ser is None:
                    self._open_serial()
                if self._ser is None:
                    return False
                self._ser.reset_input_buffer()
                self._ser.write(frame)
                self._ser.flush()
                return True
            except Exception as e:
                _LOGGER.error("发送失败: %s", e)
                self._ser = None
                return False

    def _read_loop(self):
        self._open_serial()
        buf = bytearray()
        _LOGGER.info("门禁总线监听启动")
        while self._running:
            try:
                chunk = self._ser.read(128) if self._ser else b""
            except Exception:
                self._ser = None
                time.sleep(1)
                self._open_serial()
                continue
            if not chunk:
                if self._ser is None:
                    time.sleep(1)
                    self._open_serial()
                continue
            buf += chunk
            while len(buf) >= 14 and buf[0] == 0x55:
                parsed = parse_frame(buf[:14])
                if parsed:
                    self._process_frame(parsed)
                    buf = buf[14:]
                else:
                    idx = buf.find(b"\x55", 1)
                    if idx < 0:
                        buf.clear()
                        break
                    buf = buf[idx:]
            if len(buf) > 512:
                buf.clear()

    def _process_frame(self, parsed: dict):
        cmd = parsed["cmd"]
        new_states = {}
        if cmd == CMD_CALL_START:
            self.call_active = True
            new_states["call_active"] = True
        elif cmd == CMD_HANGUP:
            self.call_active = False
            self.video_active = False
            new_states["call_active"] = False
            new_states["video_active"] = False
        elif cmd == CMD_ACK:
            if parsed["payload"].startswith(b"\x00\x00\x00\x00"):
                self.video_active = True
                new_states["video_active"] = True
        elif cmd == CMD_ANSWER:
            self.unlocked = True
            self.unlock_time = time.time()
            new_states["unlocked"] = True
        if new_states:
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.bus.async_fire(f"{DOMAIN}_state_change", new_states)
            )

    def monitor(self) -> bool:
        return self.send(MONITOR)

    def unlock(self) -> bool:
        self.send(MONITOR)
        time.sleep(1)
        self.send(UNLOCK34)
        time.sleep(0.5)
        try:
            d = self._ser.read(256) if self._ser else b""
            return d and b"\x55\x39" in d
        except Exception:
            return False

    def unlock_call(self) -> bool:
        return self.send(UNLOCK34)
