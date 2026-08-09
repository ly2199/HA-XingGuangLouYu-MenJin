"""星光楼宇 FME3 门禁协议常量与帧构建。"""
import struct

DOMAIN = "menjin"
DEVICE_ID = bytes([0x16, 0x02])  # 分机 1602
BAUD = 1200
PORT = "/dev/ttyACM0"
READ_TIMEOUT = 0.1

def mkframe(cmd: int, payload: bytes = b"\x00" * 9) -> bytes:
    """构建分机→主机帧: 55 cmd [9B载荷] 16 02 ck"""
    body = struct.pack("BB", 0x55, cmd) + payload + DEVICE_ID
    ck = sum(body) & 0xFF
    return body + struct.pack("B", ck)

def parse_frame(data: bytes) -> dict | None:
    """解析总线帧, 返回 {cmd, payload, id, raw} 或 None"""
    if len(data) < 4 or data[0] != 0x55:
        return None
    cmd = data[1]
    # 在帧中查找设备 ID 位置
    id_pos = data.find(DEVICE_ID)
    if id_pos < 0:
        return None
    payload = data[2:id_pos]
    ck = data[-1]
    calc = sum(data[:-1]) & 0xFF
    if calc != ck:
        return None
    return {"cmd": cmd, "payload": payload, "raw": data}

# 预定义帧
MONITOR  = mkframe(0x35)
UNLOCK34 = mkframe(0x34, b"\x00\x01\x01" + b"\x00" * 6)

# 主机帧命令字
CMD_CALL_START = 0x30  # 呼叫开始
CMD_RING       = 0x32  # 振铃
CMD_ANSWER     = 0x34  # 接听/开锁
CMD_MONITOR    = 0x35  # 监视请求
CMD_ACK        = 0x39  # 主机应答
CMD_HANGUP     = 0x3a  # 挂机
