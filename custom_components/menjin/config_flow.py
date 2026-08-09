"""配置流: 使集成可通过 UI 添加 (无需 configuration.yaml)。"""
from homeassistant.config_entries import ConfigFlow
from .const import DOMAIN
import voluptuous as vol

class MenjinConfigFlow(ConfigFlow, domain=DOMAIN):
    """门禁集成配置流 —— 无需用户输入, 自动创建条目。"""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="星光楼宇门禁", data={})
