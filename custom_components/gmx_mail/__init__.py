"""The GMX Mail integration."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.ssl import create_client_context

from .client.errors import GmxAuthError, GmxMailError, GmxTlsError
from .client.folders import FolderMap, map_special_folders
from .client.idle import IdleWatcher
from .client.imap import ConnectionLimiter, ImapSettings, ImapWorker, ServerInfo, tls_context_with_minimum
from .client.smtp import SmtpSettings
from .const import (
    CONF_EMAIL,
    CONF_IDLE_ENABLED,
    CONF_IDLE_RENEW_MINUTES,
    CONF_IMAP_HOST,
    CONF_IMAP_PORT,
    CONF_IMAP_SECURITY,
    CONF_PASSWORD,
    CONF_SMTP_HOST,
    CONF_SMTP_PORT,
    CONF_SMTP_SECURITY,
    DOMAIN,
    IDLE_CHECK_INTERVAL_S,
    ISSUE_FOLDER_MAPPING_AMBIGUOUS,
    ISSUE_IMAP_POSSIBLY_DISABLED,
    ISSUE_SERVER_RULES_UNAVAILABLE,
    ISSUE_UIDVALIDITY_CHANGED,
    PushState,
    Security,
)
from .coordinator import GmxMailCoordinator
from .counter import ActionCounter
from .options import folder_overrides, with_defaults
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


@dataclass(slots=True)
class GmxMailRuntimeData:
    """Objects that live as long as the config entry is loaded."""

    worker: ImapWorker
    limiter: ConnectionLimiter
    server_info: ServerInfo
    folder_map: FolderMap
    options: dict[str, Any]
    coordinator: GmxMailCoordinator
    counter: ActionCounter
    idle: IdleWatcher | None = None
    push_unavailable: PushState | None = None  # DISABLED or UNSUPPORTED when no watcher runs

    @property
    def push_state(self) -> PushState:
        """Return the state of push updates."""
        if self.idle is not None:
            return self.idle.state
        return self.push_unavailable or PushState.DISABLED


type GmxMailConfigEntry = ConfigEntry[GmxMailRuntimeData]


def imap_settings_from_data(data: Mapping[str, Any]) -> ImapSettings:
    """Build IMAP settings from config entry data."""
    return ImapSettings(
        host=data[CONF_IMAP_HOST],
        port=int(data[CONF_IMAP_PORT]),
        security=Security(data[CONF_IMAP_SECURITY]),
        username=data[CONF_EMAIL],
        password=data[CONF_PASSWORD],
    )


def smtp_settings_from_data(data: Mapping[str, Any]) -> SmtpSettings:
    """Build SMTP settings from config entry data."""
    return SmtpSettings(
        host=data[CONF_SMTP_HOST],
        port=int(data[CONF_SMTP_PORT]),
        security=Security(data[CONF_SMTP_SECURITY]),
        username=data[CONF_EMAIL],
        password=data[CONF_PASSWORD],
    )


def create_tls_context() -> ssl.SSLContext:
    """Return a new verifying TLS context (certifi bundle, TLS >= 1.2). Blocking: loads certificates."""
    return tls_context_with_minimum(create_client_context())


def issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id for a config entry."""
    return f"{ISSUE_IMAP_POSSIBLY_DISABLED}_{entry.entry_id}"


@callback
def async_create_auth_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Explain that GMX may have switched IMAP off (indistinguishable from a wrong password)."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(entry),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_IMAP_POSSIBLY_DISABLED,
        translation_placeholders={"account": entry.title},
        learn_more_url="https://hilfe.gmx.net/pop-imap/einschalten.html",
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration and register its actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GmxMailConfigEntry) -> bool:
    """Set up a GMX account: log in once to verify access, read capabilities and folders."""
    limiter = ConnectionLimiter()
    worker = ImapWorker(
        imap_settings_from_data(entry.data),
        executor=hass.async_add_executor_job,
        ssl_context_factory=create_tls_context,
        limiter=limiter,
    )
    try:
        server_info = await worker.async_connect()
    except GmxAuthError as err:
        await worker.async_shutdown()
        async_create_auth_issue(hass, entry)
        raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth_or_imap_disabled") from err
    except GmxTlsError as err:
        await worker.async_shutdown()
        raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="tls_error") from err
    except GmxMailError as err:
        await worker.async_shutdown()
        raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="cannot_connect") from err

    ir.async_delete_issue(hass, DOMAIN, issue_id(entry))

    @callback
    def _auth_failed() -> None:
        _LOGGER.warning("GMX rejected the login; IMAP may have been switched off or the app password revoked")
        async_create_auth_issue(hass, entry)
        entry.async_start_reauth(hass)

    worker.on_auth_failed = _auth_failed

    options = with_defaults(entry.options)
    folder_map = map_special_folders(server_info.folders, folder_overrides(options))
    if folder_map.invalid_overrides:
        _LOGGER.warning(
            "Folder override(s) for %s not found on the server, automatic detection is used",
            ", ".join(sorted(role.value for role in folder_map.invalid_overrides)),
        )
    _async_update_static_issues(hass, entry, folder_map)

    counter = ActionCounter(hass, entry.entry_id)
    await counter.async_load()
    coordinator = GmxMailCoordinator(hass, entry, worker, folder_map, options)
    entry.runtime_data = GmxMailRuntimeData(
        worker=worker,
        limiter=limiter,
        server_info=server_info,
        folder_map=folder_map,
        options=options,
        coordinator=coordinator,
        counter=counter,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await worker.async_shutdown()
        raise

    async def _close_if_idle(_now: datetime) -> None:
        await worker.async_close_if_idle()

    entry.async_on_unload(async_track_time_interval(hass, _close_if_idle, timedelta(seconds=IDLE_CHECK_INTERVAL_S)))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _async_start_push(hass, entry)  # before the platforms, so the connection sensor shows the right push state
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_start_push(hass: HomeAssistant, entry: GmxMailConfigEntry) -> None:
    """Start the IDLE watcher if enabled and possible."""
    data = entry.runtime_data
    settings = imap_settings_from_data(entry.data)
    if not data.options.get(CONF_IDLE_ENABLED):
        data.push_unavailable = PushState.DISABLED
        return
    if settings.security is not Security.SSL or not data.server_info.has("IDLE"):
        # aioimaplib has no STARTTLS; without IDLE capability there is nothing to wait for. Polling continues.
        _LOGGER.info("IMAP IDLE not available (needs implicit TLS and the IDLE capability); using polling only")
        data.push_unavailable = PushState.UNSUPPORTED
        return

    context: ssl.SSLContext | None = None

    async def _ssl_context() -> ssl.SSLContext:
        nonlocal context
        if context is None:
            context = await hass.async_add_executor_job(create_tls_context)
        return context

    @callback
    def _changed() -> None:
        hass.async_create_task(data.coordinator.async_request_refresh(), eager_start=True)

    @callback
    def _state_changed() -> None:
        data.coordinator.async_update_listeners()  # the connection sensor shows the push state

    @callback
    def _auth_failed() -> None:
        async_create_auth_issue(hass, entry)
        entry.async_start_reauth(hass)

    data.idle = IdleWatcher(
        settings,
        ssl_context=_ssl_context,
        limiter=data.limiter,
        on_change=_changed,
        on_auth_failed=_auth_failed,
        renew_seconds=int(data.options[CONF_IDLE_RENEW_MINUTES]) * 60,
        folder=data.coordinator.inbox,
        on_state_change=_state_changed,
    )
    entry.async_create_background_task(hass, data.idle.async_run(), f"{DOMAIN} IDLE {entry.entry_id}")


@callback
def _async_update_static_issues(hass: HomeAssistant, entry: ConfigEntry, folder_map: FolderMap) -> None:
    """Repairs that depend only on the account layout (SPEC section 4)."""
    ambiguous_id = f"{ISSUE_FOLDER_MAPPING_AMBIGUOUS}_{entry.entry_id}"
    if folder_map.ambiguous:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ambiguous_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_FOLDER_MAPPING_AMBIGUOUS,
            translation_placeholders={
                "account": entry.title,
                "roles": ", ".join(f"{role.value} → {folder_map.get(role)}" for role in sorted(folder_map.ambiguous)),
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ambiguous_id)
    # Informational: GMX web mailer filter rules cannot be reached via IMAP (no ManageSieve, GMX-NOTES row 24).
    # Once dismissed by the user, Home Assistant keeps it dismissed.
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_SERVER_RULES_UNAVAILABLE,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_SERVER_RULES_UNAVAILABLE,
    )


async def _async_update_listener(hass: HomeAssistant, entry: GmxMailConfigEntry) -> None:
    """Reload after an options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GmxMailConfigEntry) -> bool:
    """Unload a config entry, stop IDLE (background task is cancelled by HA) and log out."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.worker.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GmxMailConfigEntry) -> None:
    """Remove leftovers of a deleted config entry (repairs, stored counters and UID state)."""
    for issue in (issue_id(entry), f"{ISSUE_FOLDER_MAPPING_AMBIGUOUS}_{entry.entry_id}", f"{ISSUE_UIDVALIDITY_CHANGED}_{entry.entry_id}"):
        ir.async_delete_issue(hass, DOMAIN, issue)
    if not hass.config_entries.async_entries(DOMAIN):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_SERVER_RULES_UNAVAILABLE)
    for key in ("actions", "state"):
        await Store[dict[str, Any]](hass, 1, f"{DOMAIN}.{entry.entry_id}.{key}").async_remove()
