"""星光楼宇 FME3 门禁 HA 集成 — 修复版 v1.1.0。
- 正确解析双向帧 (每 read 含 N×14 字节)
- 超时机制关闭视频/通话状态
- 忽略非 0x55 乱码
"""
import logging
import threading
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import (
    DOMAIN, PORT, BAUD, READ_TIMEOUT, FRAME_LEN, parse_all,
    CMD_CALL_START, CMD_ACK, CMD_UNLOCK_ANS, CMD_MONITOR, CMD_UNLOCK_F3,
    VIDEO_TIMEOUT, CALL_TIMEOUT,
    MONITOR, UNLOCK34,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LOCK, Platform.BUTTON, Platform.BINARY_SENSOR]

async def async_setup(hass, config):
    if any(e.domain == DOMAIN for e in hass.config_entries.async_entries()):
        return True
    hass.async_create_task(
        hass.config_entries.flow.async_init(DOMAIN, context={"source": "import"}, data={}))
    return True

async def async_setup_entry(hass, entry):
    hass.data.setdefault(DOMAIN, {})
    if "bus" not in hass.data[DOMAIN]:
        bus = MenjinBus(hass)
        hass.data[DOMAIN]["bus"] = bus
        bus.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

class MenjinBus:
    def __init__(self, hass):
        self.hass = hass
        self._thread = None
        self._running = False
        self._ser = None
        self._lock = threading.Lock()
        self.call_active = False
        self.video_active = False
        self.unlocked = False
        self.unlock_time = 0.0
        # 超时追踪
        self._last_video_event = 0.0
        self._last_call_event = 0.0
        # 状态变更节流
        self._last_fire = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="MenjinBus")
        self._thread.start()
        # 超时检查线程
        self._timeout_thread = threading.Thread(
            target=self._timeout_loop, daemon=True, name="MenjinTimeout")
        self._timeout_thread.start()

    def _open_serial(self):
        import serial
        try:
            self._ser = serial.Serial(PORT, BAUD, timeout=READ_TIMEOUT)
            _LOGGER.info("串口 %s 已打开", PORT)
        except Exception as e:
            _LOGGER.warning("串口打开失败: %s", e)
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

            # 解析所有帧
            for parsed in parse_all(chunk):
                self._process_frame(parsed)
            if len(buf) > 1024:
                buf.clear()

    def _process_frame(self, parsed):
        cmd = parsed["cmd"]
        now = time.time()
        new = {}

        if cmd == CMD_MONITOR:
            pass  # 监视请求 (由我们发出的, 忽略)
        elif cmd == CMD_CALL_START:
            if not self.call_active:
                self.call_active = True
                new["call_active"] = True
            if not self.video_active:
                self.video_active = True
                new["video_active"] = True
            self._last_call_event = now
            self._last_video_event = now
        elif cmd == CMD_ACK:
            if not self.video_active:
                self.video_active = True
                new["video_active"] = True
            self._last_video_event = now
        elif cmd == CMD_UNLOCK_ANS or cmd == CMD_UNLOCK_F3:
            self.unlocked = True
            self.unlock_time = now
            new["unlocked"] = True
            # 开锁意味着视频/通话活跃
            self._last_video_event = now
            if not self.video_active:
                self.video_active = True
                new["video_active"] = True

        if new:
            self._fire(new)

    def _fire(self, states):
        now = time.time()
        if now - self._last_fire < 0.5:
            return  # 节流
        self._last_fire = now
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.bus.async_fire(f"{DOMAIN}_state_change", dict(states)))

    def _timeout_loop(self):
        while self._running:
            time.sleep(10)
            now = time.time()
            new = {}
            if self.video_active and now - self._last_video_event > VIDEO_TIMEOUT:
                self.video_active = False
                new["video_active"] = False
            if self.call_active and now - self._last_call_event > CALL_TIMEOUT:
                self.call_active = False
                new["call_active"] = False
            if new:
                self._fire(new)

    def monitor(self):
        return self.send(MONITOR)

    def unlock(self):
        self.send(MONITOR)
        time.sleep(1)
        self.send(UNLOCK34)
        time.sleep(0.3)
        try:
            d = self._ser.read(256) if self._ser else b""
            return d and b"\x55\x39" in d
        except Exception:
            return False

    def unlock_call(self):
        return self.send(UNLOCK34)
