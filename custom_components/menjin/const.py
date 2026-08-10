"""星光楼宇 FME3 门禁协议常量。"""
import struct

DOMAIN = "menjin"
DEVICE_ID = bytes([0x16, 0x02])  # 默认房间号 1602 (可在集成配置中修改)
BAUD = 1200
PORT = "/dev/ttyACM0"
READ_TIMEOUT = 0.1

# 帧长度
FRAME_LEN = 14

# 命令字
CMD_CALL_START = 0x30  # 主机呼叫分机 (广播帧)
CMD_RING       = 0x32  # 振铃 (点名帧, 目标分机号在帧尾 11:13)
CMD_UNLOCK_ANS = 0x34  # 开锁/接听 (点名帧)
CMD_MONITOR    = 0x35  # 监视请求
CMD_ACK        = 0x39  # 主机应答 (广播帧)
CMD_HANGUP     = 0x3a  # 挂机 (广播帧)
CMD_UNLOCK_F3  = 0xf3  # 通话中开锁状态 (点名帧)

# 点名帧: 帧尾 devid 字段(data[11:13])指向具体分机, 必须匹配本机才处理
TARGETED_CMDS = (CMD_RING, CMD_UNLOCK_ANS, CMD_UNLOCK_F3)
# 广播帧: 总线级事件, 所有分机共享, 不按 devid 过滤
BROADCAST_CMDS = (CMD_CALL_START, CMD_ACK, CMD_HANGUP)

# 状态超时 (秒)
VIDEO_TIMEOUT  = 15  # 视频通道无活动后超时关闭
CALL_TIMEOUT   = 15  # 通话无活动后超时关闭

# 预定义帧
MONITOR   = struct.pack("BB", 0x55, CMD_MONITOR) + b"\x00" * 9 + DEVICE_ID
MONITOR  += struct.pack("B", sum(MONITOR) & 0xFF)

UNLOCK34  = struct.pack("BB", 0x55, CMD_UNLOCK_ANS) + b"\x00\x01\x01" + b"\x00" * 6 + DEVICE_ID
UNLOCK34 += struct.pack("B", sum(UNLOCK34) & 0xFF)


def parse_device_id(text: str) -> bytes | None:
    """把用户输入解析为设备ID字节.

    支持 "1602" / "16-02" / "16 02" / "0x16 0x02" 等格式.
    """
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


def parse_frame(data: bytes) -> dict | None:
    """解析 14 字节帧 (不验证校验和, 主机ACK使用非标准校验算法)."""
    if len(data) < FRAME_LEN or data[0] != 0x55:
        return None
    cmd = data[1]
    payload = data[2:11]
    devid = data[11:13]
    return {"cmd": cmd, "payload": payload, "devid": devid, "raw": data}


def parse_all(data: bytes) -> list[dict]:
    """从字节流中提取所有有效帧."""
    frames = []
    i = 0
    while i + FRAME_LEN <= len(data):
        chunk = data[i:i + FRAME_LEN]
        parsed = parse_frame(chunk)
        if parsed:
            frames.append(parsed)
            i += FRAME_LEN
        else:
            # 找下一个 0x55
            idx = data.find(b"\x55", i + 1)
            if idx < 0:
                break
            i = idx
    return frames
