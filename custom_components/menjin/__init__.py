"""星光楼宇 FME3 门禁控制器 HA 自定义集成。
后台线程持续监听 RS485 总线, 实时追踪呼叫/视频/开锁状态。
"""
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
    """YAML 配置入口."""
    if DOMAIN in hass.data and "bus" in hass.data[DOMAIN]:
        return True
    return await _init(hass, entry=None)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """UI 配置入口."""
    return await _init(hass, entry=entry)

async def _init(hass: HomeAssistant, entry: ConfigEntry | None):
    hass.data.setdefault(DOMAIN, {})
    if "bus" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["bus"] = MenjinBus(hass)
        hass.data[DOMAIN]["bus"].start()
    if entry is not None:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    else:
        for platform in PLATFORMS:
            hass.async_create_task(
                hass.helpers.discovery.async_load_platform(
                    platform, DOMAIN, {}, {}
                )
            )
    return True


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

    def _init_serial(self):
        import serial
        self._ser = serial.Serial(PORT, BAUD, timeout=READ_TIMEOUT)

    def send(self, frame: bytes) -> bool:
        with self._lock:
            try:
                if self._ser is None:
                    self._init_serial()
                self._ser.reset_input_buffer()
                self._ser.write(frame)
                self._ser.flush()
                return True
            except Exception as e:
                _LOGGER.error("发送失败: %s", e)
                self._ser = None
                return False

    def _read_loop(self):
        self._init_serial()
        buf = bytearray()
        _LOGGER.info("门禁总线监听启动 (%s @ %d)", PORT, BAUD)
        while self._running:
            try:
                chunk = self._ser.read(128) if self._ser else b""
            except Exception as e:
                _LOGGER.warning("串口读错误: %s", e)
                self._ser = None
                time.sleep(1)
                try:
                    self._init_serial()
                except Exception:
                    continue
                continue
            if chunk:
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
        _LOGGER.debug("收到帧 cmd=0x%02x payload=%s", cmd, parsed["payload"].hex())
        self.last_event = parsed
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
        self.send(MONITOR)
        time.sleep(0.5)
        try:
            d = self._ser.read(128) if self._ser else b""
            if d:
                parsed = parse_frame(d)
                return parsed is not None
        except Exception:
            pass
        return False

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
