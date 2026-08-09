"""门禁锁实体。"""
import asyncio
import time
import logging
from homeassistant.components.lock import LockEntity, LockEntityFeature
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([MenjinLock()])

class MenjinLock(LockEntity):
    _attr_has_entity_name = True
    _attr_supported_features = LockEntityFeature.OPEN
    _attr_name = None
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
    }

    def __init__(self):
        self._attr_unique_id = f"{DOMAIN}_lock"
        self._unlock_time = 0.0
        self._attr_is_locked = True

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._bus = self.hass.data[DOMAIN]["bus"]
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_state_change", self._on_unlocked)
        )
        self.hass.loop.create_task(self._auto_relock())

    async def _auto_relock(self):
        while True:
            await asyncio.sleep(1)
            if not self._attr_is_locked and time.time() - self._unlock_time > 5:
                self._attr_is_locked = True
                self.async_write_ha_state()

    def _on_unlocked(self, event):
        if event.data.get("unlocked"):
            self._unlock_time = time.time()
            self._attr_is_locked = False
            self.schedule_update_ha_state()

    @property
    def is_locked(self):
        return self._attr_is_locked

    def unlock(self, **kwargs):
        """HA 在 executor 线程调用. 状态由总线回显事件驱动, 这里只负责发送."""
        self._unlock_time = time.time()
        if self._bus.video_active or self._bus.call_active:
            ok = self._bus.unlock_call()
        else:
            ok = self._bus.unlock()
        if not ok:
            _LOGGER.error("开锁指令发送失败")
        # 不直接改状态: 成功开锁由总线回显 0x34/0xf3 事件更新
