"""星光楼宇 FME3 门禁协议常量。"""
import struct

DOMAIN = "menjin"
DEVICE_ID = bytes([0x16, 0x02])
BAUD = 1200
PORT = "/dev/ttyACM0"
READ_TIMEOUT = 0.1

# 帧长度
FRAME_LEN = 14

# 命令字
CMD_CALL_START = 0x30  # 主机呼叫分机
CMD_RING       = 0x32  # 振铃
CMD_UNLOCK_ANS = 0x34  # 开锁/接听
CMD_MONITOR    = 0x35  # 监视请求
CMD_ACK        = 0x39  # 主机应答
CMD_HANGUP     = 0x3a  # 挂机 (实际未观测到)
CMD_UNLOCK_F3  = 0xf3  # 通话中开锁状态

# 状态超时 (秒)
VIDEO_TIMEOUT  = 120  # 视频通道无活动后超时关闭
CALL_TIMEOUT   = 120  # 通话无活动后超时关闭

# 预定义帧
MONITOR   = struct.pack("BB", 0x55, CMD_MONITOR) + b"\x00" * 9 + DEVICE_ID
MONITOR  += struct.pack("B", sum(MONITOR) & 0xFF)

UNLOCK34  = struct.pack("BB", 0x55, CMD_UNLOCK_ANS) + b"\x00\x01\x01" + b"\x00" * 6 + DEVICE_ID
UNLOCK34 += struct.pack("B", sum(UNLOCK34) & 0xFF)

def parse_frame(data: bytes) -> dict | None:
    """解析 14 字节帧."""
    if len(data) < FRAME_LEN or data[0] != 0x55:
        return None
    cmd = data[1]
    payload = data[2:11]
    devid = data[11:13]
    ck = data[13]
    if sum(data[:13]) & 0xFF != ck:
        return None
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
