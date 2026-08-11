"""星光楼宇 FME3 门禁控制器 HA 自定义集成 (v2 协议支持).

基于 PROTOCOL.md v2.1:
- 帧头/命令字节容错 (位翻转干扰还原)
- 13B 变体帧解析 + 跨 read 拼接
- 点名帧按设备ID过滤 (邻居活动不误触发)
- 流程状态机: 呼叫→振铃→[接听]→开锁→挂机, 幂等处理重复帧
- 完整总线日志 (R/T 方向) + 1小时心跳
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
    DOMAIN, PORT, BAUD, READ_TIMEOUT, FRAME_LEN, FRAME_LEN_13,
    try_parse_frame, try_parse_frame_13, parse_device_id,
    CMD_ACK, CMD_UNLOCK, CMD_MONITOR, CMD_UNLOCK_F3, CMD_RING,
    CMD_HANGUP, CMD_ANSWER, CMD_CALL_START, CMD_TIMEOUT, CMD_AMBIGUOUS,
    CMD_NAMES, ACCEPT_HEADS, DROP_HEADS, validate_checksum,
    VIDEO_TIMEOUT, CALL_TIMEOUT, MONITOR, UNLOCK34, ANSWER, DEVICE_ID,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LOCK, Platform.BUTTON, Platform.BINARY_SENSOR,
             Platform.SENSOR, Platform.EVENT]

# 幂等窗口 (秒)
RING_DEBOUNCE = 3.0      # 双振铃/重复振铃
UNLOCK_DEBOUNCE = 5.0    # 重复开锁帧
HANGUP_DEBOUNCE = 2.0    # 重复挂机
HEARTBEAT_INTERVAL = 3600  # 心跳间隔: 1 小时


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
        _LOGGER.info("门禁插件启动, 本机房间号=%s", device_id.hex())
        bus = MenjinBus(hass, device_id)
        hass.data[DOMAIN]["bus"] = bus
        bus.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
    """串口总线读写器 + 协议状态机 (后台线程)."""

    def __init__(self, hass: HomeAssistant, device_id: bytes):
        self.hass = hass
        self.device_id = device_id
        self._thread: threading.Thread | None = None
        self._timeout_thread: threading.Thread | None = None
        self._running = False
        self._ser = None
        self._lock = threading.Lock()
        # 状态
        self.call_active = False
        self.video_active = False
        self.answered = False
        self.unlocked = False
        self.unlock_time = 0.0
        self._flow_id = b""              # 当前活动流程的分机ID (流程锚点)
        self._last_video_event = 0.0
        self._last_call_event = 0.0
        self._last_ring_time = 0.0
        self._last_hangup_time = 0.0
        self._last_event = "无"
        self._last_event_time = 0.0
        self._last_caller = ""
        # 统计
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._parsed_frames = 0
        self._suspect_frames = 0    # 干扰帧 (命令字节还原/13B)
        self._dropped_bytes = 0     # 丢弃字节
        self._last_rx_time = 0.0
        self._last_heartbeat = 0.0
        self._log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "menjin_frames")
        try:
            os.makedirs(self._log_dir, exist_ok=True)
        except Exception:
            self._log_dir = None

    # ── 生命周期 ────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._open_serial()
        if self._ser:
            _LOGGER.info("串口 %s 已就绪, 总线监听启动 (本机 %s)", PORT, self.device_id.hex())
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
                self._log_raw(frame, "T")
                return True
            except Exception as e:
                _LOGGER.error("发送失败: %s", e)
                self._ser = None
                return False

    # ── 读取与帧解析 (容错 + 拼接) ─────────────────────
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
            while len(buf) >= FRAME_LEN_13:
                b0 = buf[0]
                if b0 in DROP_HEADS or b0 not in ACCEPT_HEADS:
                    # 无效帧头: 丢弃 1 字节继续找
                    buf.pop(0)
                    consumed += 1
                    self._dropped_bytes += 1
                    continue
                # 优先尝试 14B
                parsed = None
                if len(buf) >= FRAME_LEN:
                    parsed = try_parse_frame(bytes(buf[:FRAME_LEN]))
                if parsed is None and len(buf) >= FRAME_LEN_13:
                    parsed = try_parse_frame_13(bytes(buf[:FRAME_LEN_13]))
                if parsed is not None:
                    # 校验标记: 0x32/0x34 干净帧高置信; 校验失败多为位翻转干扰, 仍接受但计数
                    parsed["checksum_ok"] = validate_checksum(parsed["raw"])
                    if not parsed["checksum_ok"]:
                        parsed["suspect"] = True
                    self._process_frame(parsed)
                    n = FRAME_LEN if len(parsed["raw"]) == FRAME_LEN else FRAME_LEN_13
                    consumed += n
                    del buf[:n]
                    continue
                # 解析失败: 帧头可能为伪帧头, 丢弃 1 字节
                buf.pop(0)
                consumed += 1
                self._dropped_bytes += 1
            if consumed > 0:
                buf = buf[consumed:]
            if len(buf) > 1024:
                buf.clear()

    # ── 状态机 ──────────────────────────────────────────
    def _process_frame(self, parsed: dict):
        cmd = parsed["cmd"]
        devid = parsed["devid"]
        devid2 = parsed.get("devid2")
        self._parsed_frames += 1
        if parsed.get("suspect"):
            self._suspect_frames += 1
        now = time.time()
        new = {}
        me = self.device_id

        # 广播/点名判定: 点名帧必须匹配本机
        is_me = devid == me or (devid2 is not None and devid2 == me)

        if cmd == CMD_MONITOR:
            # 监视请求回显, 状态由主机 ACK 确认
            pass

        elif cmd == CMD_CALL_START:
            # 0x30 呼叫 (点名帧): 只有呼叫本机才触发
            if is_me:
                self._flow_id = me
                if not self.call_active:
                    self.call_active = True
                    new["call_active"] = True
                if not self.video_active:
                    self.video_active = True
                    new["video_active"] = True
                self._last_call_event = now
                self._last_video_event = now
                self._set_last("呼叫本机", devid.hex() if devid == me else devid2.hex())
                self._fire_event("call", caller=(devid.hex() if devid == me else devid2.hex()))

        elif cmd == CMD_RING:
            # 0x32 振铃 (点名帧): 本机才触发, 双振铃幂等
            if devid == me:
                if now - self._last_ring_time < RING_DEBOUNCE:
                    return  # 双振铃, 忽略第二次
                self._last_ring_time = now
                self._flow_id = me
                self.call_active = True
                self.video_active = True
                new["call_active"] = True
                new["video_active"] = True
                self._last_call_event = now
                self._last_video_event = now
                self._set_last("来电振铃", devid.hex())
                self._fire_event("ring", caller=devid.hex())

        elif cmd == CMD_ANSWER:
            # 0x33 接听 (点名帧): 本机接听; 挂机后出现则忽略
            if devid == me:
                if now - self._last_hangup_time < HANGUP_DEBOUNCE:
                    return  # 0x3a 之后出现, 边界忽略
                if not self.answered:
                    self.answered = True
                    new["answered"] = True
                self._set_last("接听", devid.hex())
                self._fire_event("answer", caller=devid.hex())

        elif cmd == CMD_ACK:
            # 0x39 主机应答 (点名帧, ID@6-7): 本机监视确认
            if devid == me:
                if not self.video_active:
                    self.video_active = True
                    new["video_active"] = True
                self._last_video_event = now
                self._fire_event("video", caller=devid.hex())

        elif cmd == CMD_HANGUP:
            # 0x3a/0x3e 挂机: 按流程 ID 过滤 (v2.3)
            # 只有本机挂机 或 全0广播 才复位; 邻居挂机不影响本机状态
            is_broadcast = (devid == b"\x00\x00" and devid2 == b"\x00\x00")
            if not (devid == me or devid2 == me or is_broadcast):
                return  # 邻居挂机, 忽略
            if (self.call_active or self.video_active or self.answered):
                if now - self._last_hangup_time < HANGUP_DEBOUNCE:
                    return
                self._last_hangup_time = now
                self.call_active = False
                self.video_active = False
                new["call_active"] = False
                new["video_active"] = False
                if self.answered:
                    self.answered = False
                    new["answered"] = False
                self._flow_id = b""
                self._set_last("挂机", devid.hex() if devid != b"\x00\x00" else "")
                self._fire_event("hangup", caller=devid.hex())

        elif cmd in (CMD_UNLOCK, CMD_UNLOCK_F3):
            # 0x34/0xf3 开锁 (点名帧): 本机开锁; 重复帧幂等
            if devid == me:
                if self.unlocked and now - self.unlock_time < UNLOCK_DEBOUNCE:
                    return  # 重复开锁帧, 忽略
                self.unlocked = True
                self.unlock_time = now
                new["unlocked"] = True
                self._last_video_event = now
                self._set_last("开锁", devid.hex())
                self._fire_event("unlock", caller=devid.hex())

        elif cmd == CMD_TIMEOUT:
            # 0x9a 呼叫超时: 仅记录事件, 不驱动状态
            if is_me:
                self._set_last("呼叫超时", devid.hex())
                self._fire_event("timeout", caller=devid.hex())

        else:
            _LOGGER.debug("未处理命令 0x%02x devid=%s", cmd, devid.hex())

        if new:
            self._fire_state(new)

    # ── 事件/状态分发 ───────────────────────────────────
    def _set_last(self, text: str, caller: str):
        self._last_event = text
        self._last_event_time = time.time()
        self._last_caller = caller

    def _fire_state(self, states: dict):
        _LOGGER.debug("状态变更: %s", states)
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.bus.async_fire(f"{DOMAIN}_state_change", dict(states)))

    def _fire_event(self, event_type: str, caller: str = ""):
        """门禁事件 (供 event 实体 / 自动化使用)."""
        data = {
            "type": event_type,
            "caller": caller,
            "time": time.time(),
            "event": self._last_event,
        }
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.bus.async_fire(f"{DOMAIN}_event", dict(data)))

    # ── 日志 ────────────────────────────────────────────
    def _log_raw(self, data: bytes, direction: str = "R"):
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
                f.flush()
        except Exception:
            pass

    def _log_heartbeat(self):
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
            line = (f"{ts} H 心跳:监听中 累计R={self._rx_bytes}B T={self._tx_bytes}B "
                    f"帧={self._parsed_frames} 干扰={self._suspect_frames} "
                    f"丢弃={self._dropped_bytes}B | {idle}\n")
            with open(path, "a") as f:
                f.write(line)
                f.flush()
        except Exception:
            pass

    # ── 超时与心跳 ──────────────────────────────────────
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
            if self.answered and now - self._last_call_event > CALL_TIMEOUT:
                self.answered = False
                new["answered"] = False
            if new:
                self._fire_state(new)
            if now - self._last_heartbeat >= HEARTBEAT_INTERVAL:
                self._last_heartbeat = now
                self._log_heartbeat()

    # ── 对外操作 ────────────────────────────────────────
    def monitor(self) -> bool:
        return self.send(MONITOR)

    def unlock(self) -> bool:
        """空闲开锁: 先监视建立通道, 再发开锁."""
        ok = self.send(MONITOR)
        time.sleep(1)
        ok = self.send(UNLOCK34) and ok
        return ok

    def unlock_call(self) -> bool:
        """通话/视频中开锁."""
        return self.send(UNLOCK34)

    def answer(self) -> bool:
        """模拟室内机接听: 发送 0x33 接听指令 (用于远程接听访客呼叫).

        仅在主机呼叫本机时有效 (0x33 帧 ID 必须匹配被叫分机).
        """
        return self.send(ANSWER)

    def stats(self) -> dict:
        return {
            "rx_bytes": self._rx_bytes,
            "tx_bytes": self._tx_bytes,
            "frames": self._parsed_frames,
            "suspect_frames": self._suspect_frames,
            "dropped_bytes": self._dropped_bytes,
            "last_event": self._last_event,
            "last_caller": self._last_caller,
            "last_event_time": self._last_event_time,
        }
