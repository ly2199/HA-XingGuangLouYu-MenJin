"""配置流: 支持 UI 添加和 YAML 导入, 可配置本机房间号(设备ID)."""
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_DEVICE_ID

from .const import DOMAIN, DEVICE_ID, parse_device_id

DEFAULT_DEVICE_ID = DEVICE_ID.hex()  # "1602"


class MenjinConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            devid = parse_device_id(user_input.get(CONF_DEVICE_ID, ""))
            if devid is None:
                errors = {"base": "invalid_device_id"}
            else:
                return self.async_create_entry(
                    title="星光楼宇门禁",
                    data={CONF_DEVICE_ID: devid.hex()},
                )
        schema = vol.Schema(
            {vol.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): str}
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_import(self, user_input=None):
        """YAML 导入入口 (向后兼容, 默认 1602)."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        devid = DEVICE_ID
        if user_input and CONF_DEVICE_ID in user_input:
            devid = parse_device_id(user_input[CONF_DEVICE_ID]) or DEVICE_ID
        return self.async_create_entry(
            title="星光楼宇门禁", data={CONF_DEVICE_ID: devid.hex()}
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MenjinOptionsFlow(config_entry)


class MenjinOptionsFlow(OptionsFlow):
    """选项流: 修改房间号后集成自动重载生效."""

    def __init__(self, config_entry: ConfigEntry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}
        current = self._config_entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)
        if user_input is not None:
            devid = parse_device_id(user_input.get(CONF_DEVICE_ID, ""))
            if devid is None:
                errors = {"base": "invalid_device_id"}
            else:
                return self.async_create_entry(
                    title="", data={CONF_DEVICE_ID: devid.hex()}
                )
        schema = vol.Schema(
            {vol.Required(CONF_DEVICE_ID, default=current): str}
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
