"""按钮实体: 监视 / 开锁(通话中)。"""
from homeassistant.components.button import ButtonEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([MonitorButton(hass), UnlockButton(hass), AnswerButton(hass)])

class _Base(ButtonEntity):
    _attr_has_entity_name = True
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
    }

class MonitorButton(_Base):
    _attr_unique_id = f"{DOMAIN}_monitor"
    _attr_name = "监视"
    _attr_icon = "mdi:monitor-eye"

    def __init__(self, hass):
        self._bus = hass.data[DOMAIN]["bus"]

    async def async_press(self):
        await self.hass.async_add_executor_job(self._bus.monitor)

class UnlockButton(_Base):
    _attr_unique_id = f"{DOMAIN}_unlock"
    _attr_name = "开锁(通话中)"
    _attr_icon = "mdi:lock-open"

    def __init__(self, hass):
        self._bus = hass.data[DOMAIN]["bus"]

    async def async_press(self):
        await self.hass.async_add_executor_job(self._bus.unlock_call)


class AnswerButton(_Base):
    """接听按钮: 发送 0x33 模拟室内机接听 (访客呼叫时远程接听)."""

    _attr_unique_id = f"{DOMAIN}_answer"
    _attr_name = "接听"
    _attr_icon = "mdi:phone-in-talk"

    def __init__(self, hass):
        self._bus = hass.data[DOMAIN]["bus"]

    async def async_press(self):
        await self.hass.async_add_executor_job(self._bus.answer)
