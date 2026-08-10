"""状态传感器: 最后事件 / 总线统计."""
import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        MenjinLastEvent(),
        MenjinBusStats(),
    ])


class MenjinLastEvent(SensorEntity):
    """最后一条门禁事件描述 (含呼叫方/时间/统计属性)."""

    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_last_event"
    _attr_name = "最后事件"
    _attr_icon = "mdi:history"
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
    }

    def __init__(self):
        self._attr_native_value = "无"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._bus = self.hass.data[DOMAIN]["bus"]
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_event", self._on_event)
        )

    def _on_event(self, event):
        self._attr_native_value = self._bus._last_event
        ts = self._bus._last_event_time
        self._attr_extra_state_attributes = {
            "caller": self._bus._last_caller,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else None,
        }
        self.schedule_update_ha_state()


class MenjinBusStats(SensorEntity):
    """总线采集统计 (帧数/干扰/丢弃)."""

    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_bus_stats"
    _attr_name = "总线统计"
    _attr_icon = "mdi:chart-line"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
    }

    def __init__(self):
        self._attr_native_value = "0"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._bus = self.hass.data[DOMAIN]["bus"]
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_event", self._refresh)
        )

    def _refresh(self, _event=None):
        s = self._bus.stats()
        self._attr_native_value = str(s["frames"])
        total = s["frames"] + s["suspect_frames"]
        health = "正常"
        if s["suspect_frames"] and total:
            ratio = s["suspect_frames"] / max(total, 1)
            health = f"需关注({ratio:.0%}干扰)" if ratio > 0.2 else "正常"
        self._attr_extra_state_attributes = {
            "累计帧数": s["frames"],
            "干扰帧": s["suspect_frames"],
            "丢弃字节": s["dropped_bytes"],
            "接收字节": s["rx_bytes"],
            "发送字节": s["tx_bytes"],
            "总线健康": health,
        }
        self.schedule_update_ha_state()
