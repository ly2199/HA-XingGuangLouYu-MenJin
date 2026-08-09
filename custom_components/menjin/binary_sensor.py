"""状态传感器: 来电呼叫 / 视频通道 / 锁状态。"""
import asyncio
from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        MenjinBinary("call", "来电呼叫", "mdi:phone-incoming"),
        MenjinBinary("video", "视频通道", "mdi:video"),
        MenjinBinary("locked", "已开锁", "mdi:lock-open", reset_after=5),
    ])

class MenjinBinary(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
    }

    def __init__(self, key, name, icon, reset_after=0):
        self._key = key
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_is_on = False
        self._reset_after = reset_after

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_state_change", self._on_state)
        )

    def _on_state(self, event):
        if self._key == "call":
            self._attr_is_on = event.data.get("call_active", False)
        elif self._key == "video":
            self._attr_is_on = event.data.get("video_active", False)
        elif self._key == "locked":
            if event.data.get("unlocked"):
                self._attr_is_on = True
                if self._reset_after > 0:
                    self.hass.loop.create_task(self._reset_later())
        self.schedule_update_ha_state()

    async def _reset_later(self):
        await asyncio.sleep(self._reset_after)
        self._attr_is_on = False
        self.schedule_update_ha_state()
