"""星光楼宇 FME3 门禁控制器 HA 自定义集成。
后台线程持续监听 RS485 总线, 实时追踪呼叫/视频/开锁状态。
仅处理发给本分机(房间号/设备ID)的帧, 忽略其他分机的活动。
"""
import datetime
import logging
import os
import threading
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_DEVICE_ID, Platform

from .const import (
    DOMAIN, PORT, BAUD, READ_TIMEOUT, FRAME_LEN, parse_frame, parse_device_id,
    CMD_ACK, CMD_UNLOCK_ANS, CMD_MONITOR, CMD_UNLOCK_F3,
    CMD_RING, CMD_HANGUP, TARGETED_CMDS,
    VIDEO_TIMEOUT, CALL_TIMEOUT,
    MONITOR, UNLOCK34, DEVICE_ID,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LOCK, Platform.BUTTON, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: dict):
    if any(e.domain == DOMAIN for e in hass.config_entries.async_entries()):
        return True
    hass.async_create_task(
        hass.config_entries.flow.async_init(DOMAIN, context={"source": "import"}, data={}))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})
    if "bus" not in hass.data[DOMAIN]:
        devid_text = entry.data.get(
            CONF_DEVICE_ID, entry.options.get(CONF_DEVICE_ID, DEVICE_ID.hex()))
        device_id = parse_device_id(devid_text) or DEVICE_ID
        _LOGGER.info("门禁插件启动, 本机房间号=%s (devid=%s)",
                     device_id.hex(), device_id.hex())
        bus = MenjinBus(hass, device_id)
        hass.data[DOMAIN]["bus"] = bus
        bus.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # 选项(房间号)修改后自动重载, 让新配置立即生效
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry):
    _LOGGER.info("门禁配置已更新, 重载集成...")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    bus = hass.data[DOMAIN].get("bus")
    if bus:
        bus.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


class MenjinBus:
    """串口总线读写器 (后台线程)."""

    def __init__(self, hass: HomeAssistant, device_id: bytes):
        self.hass = hass
        self.device_id = device_id  # 本机分机号 (如 b'\x16\x02' = 1602)
        self._thread: threading.Thread | None = None
        self._timeout_thread: threading.Thread | None = None
        self._running = False
        self._ser = None
        self._lock = threading.Lock()
        self.call_active = False
        self.video_active = False
        self.unlocked = False
        self.unlock_time = 0.0
        self._last_video_event = 0.0
        self._last_call_event = 0.0
        # 采集统计 (供心跳日志/完整性验证)
        self._rx_bytes = 0          # 累计接收字节
        self._tx_bytes = 0          # 累计发送字节
        self._parsed_frames = 0     # 累计解析出的有效帧 (含被过滤的邻居帧)
        self._last_rx_time = 0.0    # 最近一次收到数据的时间
        self._last_heartbeat = 0.0  # 最近一次心跳时间
        self._log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "menjin_frames")
        try:
            os.makedirs(self._log_dir, exist_ok=True)
        except Exception:
            self._log_dir = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._open_serial()
        if self._ser:
            _LOGGER.info("串口 %s 已就绪，总线监听启动 (本机 %s)", PORT, self.device_id.hex())
        else:
            _LOGGER.error("串口 %s 不可用", PORT)
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="MenjinBus")
        self._thread.start()
        self._timeout_thread = threading.Thread(
            target=self._timeout_loop, daemon=True, name="MenjinTimeout")
        self._timeout_thread.start()

    def stop(self):
        self._running = False
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def _open_serial(self):
        import serial
        try:
            self._ser = serial.Serial(PORT, BAUD, timeout=READ_TIMEOUT)
            # 清空串口残留缓冲, 保证日志从干净起点开始记录
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
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
                self._ser.write(frame)
                self._ser.flush()
                self._log_raw(frame, "T")  # 发送帧也完整记录
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
            self._log_raw(chunk, "R")
            buf += chunk

            consumed = 0
            while consumed + FRAME_LEN <= len(buf):
                parsed = parse_frame(bytes(buf[consumed:consumed + FRAME_LEN]))
                if parsed:
                    self._process_frame(parsed)
                    consumed += FRAME_LEN
                else:
                    nxt = buf.find(b"\x55", consumed + 1)
                    consumed = nxt if nxt >= 0 else len(buf)
                    break
            if consumed > 0:
                buf = buf[consumed:]
            if len(buf) > 512:
                buf.clear()

    def _process_frame(self, parsed: dict):
        cmd = parsed["cmd"]
        devid = parsed["devid"]
        self._parsed_frames += 1  # 统计总线上所有有效帧 (含被过滤的邻居帧)
        now = time.time()
        new = {}

        # 点名帧: 必须发给本机(房间号)才处理, 忽略邻居分机的振铃/开锁
        if cmd in TARGETED_CMDS and devid != self.device_id:
            _LOGGER.debug("忽略非本机点名帧 cmd=0x%02x devid=%s (本机=%s)",
                          cmd, devid.hex(), self.device_id.hex())
            return

        if cmd == CMD_MONITOR:
            pass  # 监视请求回显, 状态由主机的 ACK 帧确认
        elif cmd == CMD_RING:
            # 点名振铃: 确认呼叫到达本机
            if not self.call_active:
                self.call_active = True
                new["call_active"] = True
            if not self.video_active:
                self.video_active = True
                new["video_active"] = True
            self._last_call_event = now
            self._last_video_event = now
        elif cmd == CMD_ACK:
            # 主机应答(广播): 监视/视频通道建立确认
            if not self.video_active:
                self.video_active = True
                new["video_active"] = True
            self._last_video_event = now
        elif cmd == CMD_HANGUP:
            # 挂机(广播): 立即结束呼叫/视频, 无需等待超时
            if self.call_active or self.video_active:
                self.call_active = False
                self.video_active = False
                new["call_active"] = False
                new["video_active"] = False
                _LOGGER.debug("收到挂机, 呼叫/视频结束")
        elif cmd in (CMD_UNLOCK_ANS, CMD_UNLOCK_F3):
            # 点名开锁: 只有本机开锁才置位
            self.unlocked = True
            self.unlock_time = now
            new["unlocked"] = True
            self._last_video_event = now
        else:
            _LOGGER.debug("未处理命令 cmd=0x%02x devid=%s", cmd, devid.hex())

        if new:
            self._fire(new)

    def _fire(self, states: dict):
        _LOGGER.debug("状态变更: %s", states)
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.bus.async_fire(f"{DOMAIN}_state_change", dict(states)))

    def _log_raw(self, data: bytes, direction: str = "R"):
        """记录总线原始字节流 (完整记录: 标准帧/非标准帧/碎片/噪声 全部保留).

        direction: "R" = 接收 (总线 -> 本机), "T" = 发送 (本机 -> 总线)
        每行 = 一次串口读写返回的原始数据, 毫秒时间戳, 不丢任何字节.
        """
        if direction == "R":
            self._rx_bytes += len(data)
            self._last_rx_time = time.time()
        elif direction == "T":
            self._tx_bytes += len(data)
        if not self._log_dir:
            return
        try:
            today = datetime.date.today().isoformat()
            path = f"{self._log_dir}/{today}.log"
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            line = f"{ts} {direction} {data.hex(' ')}\n"
            with open(path, "a") as f:
                f.write(line)
                f.flush()  # 立即落盘, 防止进程异常退出时丢失日志
        except Exception:
            pass

    def _log_heartbeat(self):
        """每 5 分钟写一条心跳, 证明采集线程持续工作.

        用于区分"总线静默(无数据)"与"采集故障(线程死/串口断)".
        """
        if not self._log_dir:
            return
        try:
            now = time.time()
            today = datetime.date.today().isoformat()
            path = f"{self._log_dir}/{today}.log"
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            if self._last_rx_time:
                idle_min = int((now - self._last_rx_time) / 60)
                idle = f"距上次R数据 {idle_min} 分钟"
            else:
                idle = "尚未收到任何数据"
            line = (f"{ts} H 心跳:监听中 累计R={self._rx_bytes}B "
                    f"T={self._tx_bytes}B 帧={self._parsed_frames} | {idle}\n")
            with open(path, "a") as f:
                f.write(line)
                f.flush()
        except Exception:
            pass

    def _timeout_loop(self):
        while self._running:
            time.sleep(5)
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
            # 心跳: 每 300 秒写一条监听状态, 证明采集线程持续工作
            if now - self._last_heartbeat >= 300:
                self._last_heartbeat = now
                self._log_heartbeat()

    def monitor(self) -> bool:
        return self.send(MONITOR)

    def unlock(self) -> bool:
        """空闲开锁: 先监视建立通道, 再发开锁. 状态由总线事件驱动."""
        ok = self.send(MONITOR)
        time.sleep(1)
        ok = self.send(UNLOCK34) and ok
        return ok

    def unlock_call(self) -> bool:
        """通话/视频中开锁."""
        return self.send(UNLOCK34)
