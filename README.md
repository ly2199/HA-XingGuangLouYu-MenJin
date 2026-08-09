# 星光楼宇门禁 (FME3) Home Assistant 自定义集成

通过 USB RS232/485 适配器控制星光楼宇 FME3MBVC 系列可视对讲门禁分机。

## 功能

| 实体 | 类型 | 功能 |
|------|------|------|
| `lock.menjin_lock` | 锁 | 一键开锁,自动恢复锁定状态 |
| `button.menjin_monitor` | 按钮 | 打开监视/视频通道 |
| `button.menjin_unlock` | 按钮 | 通话中开锁 |
| `binary_sensor.menjin_call` | 传感器 | 主机来电呼叫状态 |
| `binary_sensor.menjin_video` | 传感器 | 视频通道活跃状态 |
| `binary_sensor.menjin_locked` | 传感器 | 开锁成功指示 |

## 协议

- 物理层: RS485 总线, 1200 波特率, 8N1
- 帧结构: `55 [命令] [9字节载荷] [ID 2字节] [校验和 1字节]`
- 校验: 前 13 字节求和 mod 256
- 设备 ID: `16 02` (分机号 1602)

## 安装

1. 复制 `custom_components/menjin/` 到 HA 的 `/config/custom_components/`
2. 在 `configuration.yaml` 中添加 `menjin:`
3. 确保 HA 用户有权访问 `/dev/ttyACM0`:
   ```bash
   sudo usermod -a -G dialout homeassistant
   ```
4. 重启 Home Assistant

## 接线

```
USB 适配器    门禁分机
  RXD    ───  D (数据)
  TXD    ───  D (通过 1kΩ~4.7kΩ 电阻)
  GND    ───  G (地)
```

## 支持设备

- 星光楼宇 FME3MBVC-A10 系列
- 星光楼宇 FME3 系列可视对讲分机
