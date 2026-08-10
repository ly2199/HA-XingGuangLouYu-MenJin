"""星光楼宇 FME3 门禁协议常量与解析工具 (v2 协议支持).

基于 PROTOCOL.md v2.1 协议分析:
- 帧头容错: 接受 0x55/0x5d/0x75/0x77 (Hamming<=2), 丢弃 0x13/0x27
- 命令字节容错: 显式观测表 + Hamming<=1 兜底 (0x26 例外)
- 13B 变体: 载荷少 1 字节, ID/校验左移 1 位
- ID 位置: 0x30@6-7, 0x32/0x33/0x34/0xf3/0x9a@11-12, 0x39/0x3a/0x3e@6-7
"""
import struct

DOMAIN = "menjin"
DEVICE_ID = bytes([0x16, 0x02])  # 默认房间号 1602 (可在集成配置中修改)
BAUD = 1200
PORT = "/dev/ttyACM0"
READ_TIMEOUT = 0.1

FRAME_LEN = 14       # 标准帧长
FRAME_LEN_13 = 13    # 13B 变体帧长

# ── 命令字 ──────────────────────────────────────────────
CMD_CALL_START = 0x30  # 呼叫 (点名帧, ID@6-7)
CMD_RING       = 0x32  # 振铃 (点名帧, ID@11-12)
CMD_ANSWER     = 0x33  # 接听/摘机 (点名帧, ID@11-12)
CMD_UNLOCK     = 0x34  # 开锁 (点名帧, ID@11-12)
CMD_MONITOR    = 0x35  # 监视请求 (分机→主机)
CMD_ACK        = 0x39  # 主机应答 (点名帧, ID@6-7)
CMD_HANGUP     = 0x3a  # 挂机/流程结束 (多数 ID@6-7, 少数@11-12)
CMD_UNLOCK_F3  = 0xf3  # 开锁动作上报 (ID@11-12)
CMD_TIMEOUT    = 0x9a  # 呼叫超时提示 (ID@11-12)

# 命令中文名 (日志/事件用)
CMD_NAMES = {
    CMD_CALL_START: "呼叫",
    CMD_RING: "振铃",
    CMD_ANSWER: "接听",
    CMD_UNLOCK: "开锁",
    CMD_MONITOR: "监视",
    CMD_ACK: "应答",
    CMD_HANGUP: "挂机",
    CMD_UNLOCK_F3: "开锁上报",
    CMD_TIMEOUT: "超时",
}

# ── 帧头容错: 观测值 -> 真实值 0x55 ─────────────────────
ACCEPT_HEADS = {0x55, 0x5d, 0x75, 0x77}  # 0x55 ^ {0x00, 0x08, 0x20, 0x22}
DROP_HEADS = {0x13, 0x27}                # 严重干扰帧头, 直接丢弃

# ── 命令字节容错: 显式观测表 (PROTOCOL.md §5.1) ─────────
CMD_FIX = {
    0x30: CMD_CALL_START, 0x70: CMD_CALL_START, 0xb0: CMD_CALL_START,  # ^0x40 ^0x80
    0x32: CMD_RING,        0x72: CMD_RING,        0x36: CMD_RING,      # ^0x40 ^0x04
    0x33: CMD_ANSWER,      0x13: CMD_ANSWER,      0x73: CMD_ANSWER,    # ^0x20 ^0x40
    0x34: CMD_UNLOCK,      0x74: CMD_UNLOCK,                           # ^0x40
    0x35: CMD_MONITOR,     0xb5: CMD_MONITOR,                          # ^0x80
    0x39: CMD_ACK,         0x3b: CMD_ACK,                              # ^0x02 (上下文判定)
    0x3a: CMD_HANGUP,      0xba: CMD_HANGUP,      0x3e: CMD_HANGUP,    # ^0x80 ^0x04
    0xf3: CMD_UNLOCK_F3,
    0x9a: CMD_TIMEOUT,
}
CMD_AMBIGUOUS = {0x3b}  # 需要按上下文判定 (ACK 槽位/挂机槽位)
CMD_DROP = {0x26}       # 0x32^0x14 距离2, 丢弃

# ── ID 字段位置 (14B 帧, 0-indexed) ─────────────────────
# 值: (start, length); 13B 帧时整体左移 1 位
ID_POS = {
    CMD_CALL_START: (6, 2),
    CMD_RING: (11, 2),
    CMD_ANSWER: (11, 2),
    CMD_UNLOCK: (11, 2),
    CMD_MONITOR: (11, 2),
    CMD_ACK: (6, 2),
    CMD_HANGUP: (6, 2),    # 部分帧在 11-12, 解析时双位置尝试
    CMD_UNLOCK_F3: (11, 2),
    CMD_TIMEOUT: (11, 2),
}
# 0x3a 需要双位置尝试 (6-7 多数, 11-12 少数)
HANGUP_ALT_POS = (11, 2)

# ── 校验和 ──────────────────────────────────────────────
def calc_checksum(data: bytes) -> int:
    """8 位累加和 (模 256): 从 0x55 到校验前一个字节累加, 取低 8 位.

    PROTOCOL.md v2.2 §2.4: 已确认 0x32/0x34 使用此算法.
    """
    return sum(data) & 0xFF


def validate_checksum(frame: bytes) -> bool:
    """校验完整 14B/13B 帧 (最后一个字节为校验)."""
    if len(frame) < 2:
        return False
    return calc_checksum(frame[:-1]) == frame[-1]


# ── 预定义帧 ────────────────────────────────────────────
def _build(cmd: int, payload: bytes) -> bytes:
    """构造 14B 帧: 55 cmd payload(9B) devid 校验(8位累加和)."""
    frame = struct.pack("BB", 0x55, cmd) + payload.ljust(9, b"\x00") + DEVICE_ID
    return frame + struct.pack("B", calc_checksum(frame))


MONITOR = _build(CMD_MONITOR, b"")
UNLOCK34 = _build(CMD_UNLOCK, b"\x00\x01\x01")
ANSWER = _build(CMD_ANSWER, b"\x00\x01\x01")  # 接听指令 (模拟室内机摘机)

# ── 超时常量 ────────────────────────────────────────────
VIDEO_TIMEOUT = 15   # 视频通道无活动后超时关闭
CALL_TIMEOUT = 20    # 呼叫无活动后超时关闭 (协议实测 16.8~31s)


def hamming(a: int, b: int) -> int:
    """两字节的汉明距离."""
    return bin(a ^ b).count("1")


def fix_command(raw: int) -> int | None:
    """命令字节容错: 显式表优先, 未知命令 Hamming<=1 兜底.

    返回真实命令字; 0x26/未知返回 None (应丢弃/记录).
    """
    if raw in CMD_FIX:
        return CMD_FIX[raw]
    if raw in CMD_DROP:
        return None
    # Hamming<=1 兜底: 找唯一候选
    known = set(CMD_FIX.values())
    candidates = [c for c in known if hamming(raw, c) <= 1]
    if len(candidates) == 1:
        return candidates[0]
    return None


def parse_device_id(text: str) -> bytes | None:
    """把用户输入解析为设备ID字节. 支持 1602 / 16-02 / 0x16 0x02 等格式."""
    if not text:
        return None
    cleaned = (
        text.strip().lower()
        .replace("0x", " ")
        .replace("-", " ")
        .replace(",", " ")
        .replace("，", " ")
    )
    parts = [p for p in cleaned.split() if p]
    try:
        if len(parts) == 1 and len(parts[0]) == 4:
            return bytes.fromhex(parts[0])
        if len(parts) == 2:
            return bytes([int(parts[0], 16), int(parts[1], 16)])
    except ValueError:
        pass
    return None


def format_device_id(devid: bytes) -> str:
    """设备ID字节转可读字符串, 如 b'\\x16\\x02' -> '1602'."""
    return devid.hex()


def extract_id(frame: bytes, cmd: int, alt: bool = False) -> bytes:
    """从帧中提取 ID 字段 (14B 帧).

    alt=True 时用备用位置 (0x3a 的 11-12).
    """
    pos = HANGUP_ALT_POS if (alt and cmd == CMD_HANGUP) else ID_POS[cmd]
    start, length = pos
    return frame[start:start + length]


def try_parse_frame(frame: bytes) -> dict | None:
    """解析单帧 (14B 标准帧).

    返回 {cmd, devid, raw, suspect} 或 None (无法解析).
    """
    if len(frame) < FRAME_LEN or frame[0] not in ACCEPT_HEADS:
        return None
    cmd_raw = frame[1]
    if cmd_raw in DROP_HEADS:
        return None
    cmd = fix_command(cmd_raw)
    if cmd is None:
        return None
    # 主 ID
    devid = extract_id(frame, cmd)
    result = {"cmd": cmd, "cmd_raw": cmd_raw, "devid": devid, "raw": frame,
              "suspect": cmd_raw != cmd}
    # 0x3a 双位置尝试: 若主位置 ID 全 0 或等于广播特征, 尝试备用
    if cmd == CMD_HANGUP:
        alt_devid = extract_id(frame, cmd, alt=True)
        if devid == b"\x00\x00" or devid == b"\x00" * 2:
            result["devid"] = alt_devid
            result["devid2"] = devid
        else:
            result["devid2"] = alt_devid
    return result


def try_parse_frame_13(frame13: bytes) -> dict | None:
    """解析 13B 变体帧 (载荷少 1 字节, ID/校验左移 1 位).

    0x30 的 13B ID 位置存疑: 返回双候选 devid/devid2 (5-6 与 6-7).
    """
    if len(frame13) < FRAME_LEN_13 or frame13[0] not in ACCEPT_HEADS:
        return None
    cmd_raw = frame13[1]
    if cmd_raw in DROP_HEADS:
        return None
    cmd = fix_command(cmd_raw)
    if cmd is None:
        return None
    # 13B: ID 位置 = 14B 位置 - 1
    pos = ID_POS[cmd]
    start, length = pos
    devid = frame13[max(0, start - 1):max(0, start - 1) + length]
    result = {"cmd": cmd, "cmd_raw": cmd_raw, "devid": devid, "raw": frame13,
              "suspect": True, "short": True}
    if cmd == CMD_CALL_START:
        # 0x30 13B: 双候选 (5-6 与 6-7)
        result["devid2"] = frame13[6:8]
    elif cmd == CMD_HANGUP:
        alt = frame13[10:12]
        result["devid2"] = alt
    return result
