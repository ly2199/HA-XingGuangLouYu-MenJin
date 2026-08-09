"""门禁锁实体 — 官方 LockEntity API 对齐。"""
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
    _attr_name = None
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
        "sw_version": "1.0",
    }

    def __init__(self, hass):
        self._attr_unique_id = f"{DOMAIN}_lock"
        self._bus = hass.data[DOMAIN]["bus"]
        self._unlock_time = 0.0
        self._attr_is_locked = True  # 使用官方 _attr_is_locked

        hass.bus.async_listen(f"{DOMAIN}_state_change", self._on_unlocked)

        async def auto_relock():
            while True:
                await asyncio.sleep(1)
                if not self._attr_is_locked and time.time() - self._unlock_time > 5:
                    self._attr_is_locked = True
                    self.async_write_ha_state()
        self.hass.loop.create_task(auto_relock())

    def _on_unlocked(self, event):
        """收到开锁事件 — 线程安全回调。"""
        if event.data.get("unlocked"):
            self._unlock_time = time.time()
            self._attr_is_locked = False
            self.schedule_update_ha_state()

    def unlock(self, **kwargs):
        """HA 框架在 executor 线程中调用此方法。"""
        self._unlock_time = time.time()
        if self._bus.video_active or self._bus.call_active:
            _LOGGER.info("开锁: 通话/视频中 → 直接 0x34")
            ok = self._bus.unlock_call()
        else:
            _LOGGER.info("开锁: 空闲 → 监视 → 0x34")
            ok = self._bus.unlock()
        self._attr_is_locked = not ok
        self.schedule_update_ha_state()
