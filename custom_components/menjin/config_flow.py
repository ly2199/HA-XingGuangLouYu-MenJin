"""配置流: 支持 UI 添加和 YAML 导入。"""
from homeassistant.config_entries import ConfigFlow
from .const import DOMAIN

class MenjinConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="星光楼宇门禁", data={})

    async def async_step_import(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="星光楼宇门禁", data={})
