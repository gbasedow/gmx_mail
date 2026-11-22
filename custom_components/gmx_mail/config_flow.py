"""Config, options, reauth and reconfigure flows for GMX Mail."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, override

import probatio
from homeassistant.config_entries import ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import GmxMailConfigEntry, create_tls_context, imap_settings_from_data, smtp_settings_from_data
from .client.errors import GmxAuthError, GmxConnectionError, GmxConnectionLimitError, GmxSmtpAuthError, GmxTlsError
from .client.imap import ServerInfo, check_imap_account
from .client.smtp import check_smtp_login
from .const import (
    CONF_ALLOW_HTML_OUTPUT,
    CONF_ALLOW_PERMANENT_DELETE,
    CONF_ALLOW_REPLY_TO_SENDER,
    CONF_ALLOWLIST,
    CONF_ATTACHMENT_RETENTION_DAYS,
    CONF_AUDIT_RETENTION_DAYS,
    CONF_EMAIL,
    CONF_EVENT_INCLUDE_HEADERS,
    CONF_IDLE_ENABLED,
    CONF_IDLE_RENEW_MINUTES,
    CONF_IMAP_HOST,
    CONF_IMAP_PORT,
    CONF_IMAP_SECURITY,
    CONF_MAX_DELETE_PER_DAY,
    CONF_MAX_MESSAGE_SIZE_MB,
    CONF_MAX_RECIPIENTS,
    CONF_MAX_RECIPIENTS_PER_HOUR,
    CONF_MAX_SEND_PER_DAY,
    CONF_PASSWORD,
    CONF_PERMISSIONS,
    CONF_POLL_INTERVAL,
    CONF_SEND_MODE,
    CONF_SENDER_NAME,
    CONF_SENT_COPY_MODE,
    CONF_SMTP_HOST,
    CONF_SMTP_PORT,
    CONF_SMTP_SECURITY,
    DEFAULT_IMAP_HOST,
    DEFAULT_IMAP_PORT,
    DEFAULT_IMAP_SECURITY,
    DEFAULT_OPTIONS,
    DEFAULT_SMTP_HOST,
    DEFAULT_SMTP_PORT,
    DEFAULT_SMTP_SECURITY,
    DOMAIN,
    FOLDER_OVERRIDE_KEYS,
    SECTION_CONNECTION,
    SECTION_FOLDERS,
    SECTION_LIMITS,
    SECTION_PERMISSIONS,
    SECTION_PRIVACY,
    SECTION_SENDING,
    SECTION_SERVER,
    Permission,
    Security,
    SendMode,
    SentCopyMode,
)
from .options import INT_RANGES, flatten, nest, validate_options, with_defaults

_LOGGER = logging.getLogger(__name__)

SERVER_KEYS = (CONF_IMAP_HOST, CONF_IMAP_PORT, CONF_IMAP_SECURITY, CONF_SMTP_HOST, CONF_SMTP_PORT, CONF_SMTP_SECURITY)
SERVER_DEFAULTS: dict[str, Any] = {
    CONF_IMAP_HOST: DEFAULT_IMAP_HOST,
    CONF_IMAP_PORT: DEFAULT_IMAP_PORT,
    CONF_IMAP_SECURITY: DEFAULT_IMAP_SECURITY.value,
    CONF_SMTP_HOST: DEFAULT_SMTP_HOST,
    CONF_SMTP_PORT: DEFAULT_SMTP_PORT,
    CONF_SMTP_SECURITY: DEFAULT_SMTP_SECURITY.value,
}

_SECURITY_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[s.value for s in Security],
        mode=SelectSelectorMode.DROPDOWN,
        translation_key="security",
    )
)
_PORT = probatio.All(
    NumberSelector(NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)),
    probatio.Coerce(int),
)
_HOST = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

SERVER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_IMAP_HOST): _HOST,
        probatio.Required(CONF_IMAP_PORT): _PORT,
        probatio.Required(CONF_IMAP_SECURITY): _SECURITY_SELECTOR,
        probatio.Required(CONF_SMTP_HOST): _HOST,
        probatio.Required(CONF_SMTP_PORT): _PORT,
        probatio.Required(CONF_SMTP_SECURITY): _SECURITY_SELECTOR,
    }
)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")),
        probatio.Required(CONF_PASSWORD): _PASSWORD,
        probatio.Required(SECTION_SERVER): section(SERVER_SCHEMA, {"collapsed": True}),
    }
)

REAUTH_SCHEMA = probatio.Schema({probatio.Required(CONF_PASSWORD): _PASSWORD})


def _normalize_server(values: Mapping[str, Any]) -> dict[str, Any]:
    server = {**SERVER_DEFAULTS, **{k: v for k, v in values.items() if k in SERVER_KEYS}}
    server[CONF_IMAP_HOST] = str(server[CONF_IMAP_HOST]).strip().lower()
    server[CONF_SMTP_HOST] = str(server[CONF_SMTP_HOST]).strip().lower()
    server[CONF_IMAP_PORT] = int(server[CONF_IMAP_PORT])
    server[CONF_SMTP_PORT] = int(server[CONF_SMTP_PORT])
    return server


def _validate_account(data: Mapping[str, Any]) -> ServerInfo:
    """Check IMAP login, CAPABILITY, LIST and the SMTP login (blocking)."""
    context = create_tls_context()
    info = check_imap_account(imap_settings_from_data(data), context)
    check_smtp_login(smtp_settings_from_data(data), context)
    return info


async def async_validate_account(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    """Validate credentials and servers; return form errors (empty on success)."""
    try:
        info = await hass.async_add_executor_job(_validate_account, data)
    except GmxAuthError:
        return {"base": "invalid_auth_or_imap_disabled"}
    except GmxSmtpAuthError:
        return {"base": "smtp_auth_failed"}
    except GmxTlsError:
        return {"base": "tls_error"}
    except GmxConnectionError, GmxConnectionLimitError:
        return {"base": "cannot_connect"}
    except Exception:  # shown as "unknown" in the form
        _LOGGER.exception("Unexpected error while validating the GMX account")
        return {"base": "unknown"}
    missing = info.missing_recommended
    if missing:
        _LOGGER.warning("IMAP server lacks %s; fallback paths will be used", ", ".join(missing))
    return {}


class GmxMailConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: GmxMailConfigEntry) -> GmxMailOptionsFlow:
        """Return the options flow."""
        return GmxMailOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for address and app password; server settings are pre-filled."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = str(user_input[CONF_EMAIL]).strip()
            if email.count("@") != 1 or not all(email.split("@")):
                errors[CONF_EMAIL] = "invalid_email"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                data = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    **_normalize_server(user_input.get(SECTION_SERVER) or {}),
                }
                errors = await async_validate_account(self.hass, data)
                if not errors:
                    return self.async_create_entry(title=email, data=data, options=dict(DEFAULT_OPTIONS))

        suggested: dict[str, Any] = {SECTION_SERVER: dict(SERVER_DEFAULTS)}
        if user_input is not None:
            suggested = {
                CONF_EMAIL: user_input.get(CONF_EMAIL),
                SECTION_SERVER: _normalize_server(user_input.get(SECTION_SERVER) or {}),
            }
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, suggested),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after a rejected login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new app password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            errors = await async_validate_account(self.hass, data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"account": entry.title},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change hosts, ports and TLS mode."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            server = _normalize_server(user_input)
            errors = await async_validate_account(self.hass, {**entry.data, **server})
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=server)
        suggested = _normalize_server(user_input if user_input is not None else entry.data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(SERVER_SCHEMA, suggested),
            errors=errors,
            description_placeholders={"account": entry.title},
        )


def _int_selector(key: str, unit: str | None = None) -> Any:
    low, high = INT_RANGES[key]
    config = NumberSelectorConfig(min=low, step=1, mode=NumberSelectorMode.BOX)
    if high is not None:
        config["max"] = high
    if unit is not None:
        config["unit_of_measurement"] = unit
    return probatio.All(NumberSelector(config), probatio.Coerce(int))


def build_options_schema(folders: list[str]) -> probatio.Schema:
    """Return the sectioned options schema; ``folders`` feeds the folder override drop-downs."""
    folder_selector: Any
    if folders:
        folder_selector = SelectSelector(SelectSelectorConfig(options=folders, custom_value=True, mode=SelectSelectorMode.DROPDOWN))
    else:
        folder_selector = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

    def _select(options: list[str], key: str, *, multiple: bool = False) -> SelectSelector:
        mode = SelectSelectorMode.LIST if multiple else SelectSelectorMode.DROPDOWN
        return SelectSelector(SelectSelectorConfig(options=options, multiple=multiple, mode=mode, translation_key=key))

    sections = {
        SECTION_PERMISSIONS: (
            {
                probatio.Required(CONF_PERMISSIONS): _select([p.value for p in Permission], CONF_PERMISSIONS, multiple=True),
                probatio.Required(CONF_ALLOW_PERMANENT_DELETE): BooleanSelector(),
                probatio.Required(CONF_ALLOW_HTML_OUTPUT): BooleanSelector(),
            },
            False,
        ),
        SECTION_SENDING: (
            {
                probatio.Required(CONF_SEND_MODE): _select([m.value for m in SendMode], CONF_SEND_MODE),
                probatio.Optional(CONF_ALLOWLIST): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiple=True)),
                probatio.Required(CONF_ALLOW_REPLY_TO_SENDER): BooleanSelector(),
                probatio.Optional(CONF_SENDER_NAME): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                probatio.Required(CONF_SENT_COPY_MODE): _select([m.value for m in SentCopyMode], CONF_SENT_COPY_MODE),
            },
            False,
        ),
        SECTION_LIMITS: (
            {
                probatio.Required(CONF_MAX_RECIPIENTS): _int_selector(CONF_MAX_RECIPIENTS),
                probatio.Required(CONF_MAX_RECIPIENTS_PER_HOUR): _int_selector(CONF_MAX_RECIPIENTS_PER_HOUR),
                probatio.Required(CONF_MAX_SEND_PER_DAY): _int_selector(CONF_MAX_SEND_PER_DAY),
                probatio.Required(CONF_MAX_MESSAGE_SIZE_MB): _int_selector(CONF_MAX_MESSAGE_SIZE_MB, "MB"),
                probatio.Required(CONF_MAX_DELETE_PER_DAY): _int_selector(CONF_MAX_DELETE_PER_DAY),
            },
            True,
        ),
        SECTION_CONNECTION: (
            {
                probatio.Required(CONF_POLL_INTERVAL): _int_selector(CONF_POLL_INTERVAL, "s"),
                probatio.Required(CONF_IDLE_ENABLED): BooleanSelector(),
                probatio.Required(CONF_IDLE_RENEW_MINUTES): _int_selector(CONF_IDLE_RENEW_MINUTES, "min"),
            },
            True,
        ),
        SECTION_FOLDERS: (
            {probatio.Optional(key): folder_selector for key in FOLDER_OVERRIDE_KEYS.values()},
            True,
        ),
        SECTION_PRIVACY: (
            {
                probatio.Required(CONF_EVENT_INCLUDE_HEADERS): BooleanSelector(),
                probatio.Required(CONF_AUDIT_RETENTION_DAYS): _int_selector(CONF_AUDIT_RETENTION_DAYS, "d"),
                probatio.Required(CONF_ATTACHMENT_RETENTION_DAYS): _int_selector(CONF_ATTACHMENT_RETENTION_DAYS, "d"),
            },
            True,
        ),
    }
    return probatio.Schema(
        {
            probatio.Required(name): section(probatio.Schema(fields), {"collapsed": collapsed})
            for name, (fields, collapsed) in sections.items()
        }
    )


class GmxMailOptionsFlow(OptionsFlow):
    """Permissions, send mode, allowlist, limits, connection and folder options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and validate the options form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = validate_options(flatten(user_input))
            if not errors:
                return self.async_create_entry(data=options)
            suggested = nest(options)
        else:
            suggested = nest(with_defaults(self.config_entry.options))

        folders: list[str] = []
        entry: GmxMailConfigEntry = self.config_entry
        if entry.state is ConfigEntryState.LOADED:
            folders = [f.name for f in entry.runtime_data.server_info.folders if f.selectable]
        # Empty overrides are left out so that the form shows an empty field ("automatic").
        for values in suggested.values():
            for key in [k for k, v in values.items() if v in ("", None)]:
                del values[key]

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(build_options_schema(folders), suggested),
            errors=errors,
        )
