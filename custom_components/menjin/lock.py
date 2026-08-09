"""门禁锁实体: 显示锁状态, 调用 unlock 服务。"""
import asyncio
import time
import logging
from homeassistant.components.lock import LockEntity, LockEntityFeature
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
        self._state = False
        self._bus = hass.data[DOMAIN]["bus"]
        hass.bus.async_listen(f"{DOMAIN}_state_change", self._on_state)

        async def auto_relock():
            while True:
                await asyncio.sleep(1)
                if self._state and time.time() - self._bus.unlock_time > 3:
                    self._state = False
                    self.async_write_ha_state()
        self.hass.loop.create_task(auto_relock())

    def _on_state(self, event):
        if event.data.get("unlocked"):
            self._state = True
            self.schedule_update_ha_state()

    @property
    def is_locked(self):
        return not self._state

    async def async_unlock(self, **kwargs):
        if self._bus.video_active or self._bus.call_active:
            _LOGGER.info("开锁: 通话/视频中, 直接发 0x34")
            ok = await self.hass.async_add_executor_job(self._bus.unlock_call)
        else:
            _LOGGER.info("开锁: 先建立监视, 再发 0x34")
            ok = await self.hass.async_add_executor_job(self._bus.unlock)
        self._state = ok
        self.async_write_ha_state()
