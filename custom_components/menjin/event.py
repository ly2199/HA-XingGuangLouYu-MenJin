"""门禁事件实体: 呼叫/振铃/接听/开锁/挂机 事件流 (供自动化使用)."""
from homeassistant.components.event import EventEntity
from homeassistant.const import EntityCategory
from .const import DOMAIN

EVENT_TYPES = ["call", "ring", "answer", "unlock", "hangup", "video", "timeout"]

EVENT_LABELS = {
    "call": "呼叫",
    "ring": "振铃",
    "answer": "接听",
    "unlock": "开锁",
    "hangup": "挂机",
    "video": "视频建立",
    "timeout": "呼叫超时",
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([MenjinEvent()])


class MenjinEvent(EventEntity):
    """门禁总线事件 (device_class 默认)."""

    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_event"
    _attr_name = "门禁事件"
    _attr_icon = "mdi:doorbell"
    _attr_event_types = EVENT_TYPES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_info = {
        "identifiers": {(DOMAIN, "menjin_lock")},
        "name": "门禁锁",
        "manufacturer": "星光楼宇",
        "model": "FME3MBVC",
    }

    def __init__(self):
        self._attr_translation_key = "menjin_event"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_event", self._on_event)
        )

    def _on_event(self, event):
        etype = event.data.get("type")
        if etype not in EVENT_TYPES:
            return
        attrs = {}
        caller = event.data.get("caller", "")
        if caller:
            attrs["caller"] = caller
            attrs["caller_name"] = "本机" if caller == "1602" else f"分机{caller}"
        attrs["事件"] = EVENT_LABELS.get(etype, etype)
        self._trigger_event(etype, attrs)
