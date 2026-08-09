"""门禁锁实体: 显示锁状态, 调用 unlock 服务。"""
import asyncio
import time
import logging
from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.const import Platform
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([MenjinLock(hass)])

class MenjinLock(LockEntity):
    _attr_has_entity_name = True
    _attr_supported_features = LockEntityFeature.OPEN
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
        "sw_version": "1.0",
    }

    def __init__(self, hass):
        self.hass = hass
        self._attr_unique_id = f"{DOMAIN}_lock"
        self._attr_name = None
        self._state = None
        bus = hass.data[DOMAIN]["bus"]
        # 注册状态变更回调
        def on_state(event):
            if event.data.get("unlocked"):
                self._state = True
                self.async_write_ha_state()
        hass.bus.async_listen(f"{DOMAIN}_state_change", on_state)

        # 自动恢复锁定状态
        async def auto_lock():
            while True:
                await self.hass.async_add_executor_job(lambda: None)  # no-op, just loop
                if self._state and time.time() - bus.unlock_time > 3:
                    self._state = False
                    self.async_write_ha_state()
                await asyncio.sleep(1)
        self.hass.loop.create_task(auto_lock())

    @property
    def is_locked(self):
        return not self._state

    async def async_open(self, **kwargs):
        """开锁."""
        bus = self.hass.data[DOMAIN]["bus"]
        ok = await self.hass.async_add_executor_job(bus.unlock)
        if ok:
            self._state = True
            self.async_write_ha_state()
