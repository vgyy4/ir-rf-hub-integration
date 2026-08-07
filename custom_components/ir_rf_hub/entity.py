from __future__ import annotations

from typing import Callable, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CommandRecord
from .const import CONF_DEVICE_GROUPING, DEFAULT_DEVICE_GROUPING, DOMAIN, MODE_SEPARATE
from .coordinator import SIGNAL_COMMAND_ADDED, SIGNAL_COMMAND_REMOVED, IrRfHubCoordinator
from .device_grouping import device_info_for


class IrRfHubCommandEntity(CoordinatorEntity[IrRfHubCoordinator]):
    """Base for the button/switch/select trio on one command. How they're
    grouped into HA Devices is user-configurable via the integration's
    options flow (see device_grouping.py) -- entity_kind is which of the
    three this instance is, needed because "split by type" mode groups
    differently per kind.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: IrRfHubCoordinator, command_id: str, entity_kind: str) -> None:
        super().__init__(coordinator)
        self._command_id = command_id
        self._entity_kind = entity_kind

    @property
    def _command(self) -> CommandRecord | None:
        return self.coordinator.data.get(self._command_id)

    @property
    def _grouping_mode(self) -> str:
        return self.coordinator.entry.options.get(CONF_DEVICE_GROUPING, DEFAULT_DEVICE_GROUPING)

    def _qualified_name(self, suffix: str) -> str:
        """In "separate" mode, this entity's device is named after its
        own command (see device_info below), so a plain per-type suffix
        ("Button", "Switch", "Send via") is enough to disambiguate --
        has_entity_name combines it with the device name automatically.
        In "unified"/"split_by_type" mode, many commands' entities share
        one device, so that plain suffix alone would be identical across
        every command's Button/Switch/Select and impossible to tell
        apart in the device's entity list -- prefixing with the command
        name keeps every entity distinguishable regardless of mode.
        """
        if self._grouping_mode == MODE_SEPARATE:
            return suffix
        command = self._command
        name = command.name if command else self._command_id
        return f"{name} {suffix}"

    @property
    def available(self) -> bool:
        return super().available and self._command is not None

    @property
    def device_info(self) -> DeviceInfo:
        return device_info_for(
            self.coordinator.entry.entry_id, self._command_id, self._command, self._entity_kind, self._grouping_mode
        )


_EntityT = TypeVar("_EntityT", bound=IrRfHubCommandEntity)


def async_setup_command_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    make_entity: Callable[[IrRfHubCoordinator, str], _EntityT],
) -> None:
    """Shared add/remove wiring for the button/switch/select platforms: one
    entity of `make_entity`'s type per Command, kept live-synced with
    SIGNAL_COMMAND_ADDED/REMOVED, with explicit entity-registry removal and
    mode-aware orphaned-device cleanup on delete. The three platforms only
    ever differed in which entity class to construct -- this is that shared
    wiring, extracted once.
    """
    coordinator: IrRfHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: dict[str, _EntityT] = {}

    @callback
    def _add_new(command: CommandRecord) -> None:
        if command.id in entities:
            return
        entity = make_entity(coordinator, command.id)
        entities[command.id] = entity
        async_add_entities([entity])

    @callback
    def _remove(command_id: str) -> None:
        entity = entities.pop(command_id, None)
        if entity is None or entity.entity_id is None:
            return
        # entity.async_remove() alone tears down the live entity/state but
        # doesn't reliably purge the entity *registry* entry -- explicit
        # registry removal is what actually makes a deleted command's
        # entities disappear for good, not just go unavailable.
        registry = er.async_get(hass)
        registry_entry = registry.async_get(entity.entity_id)
        device_id = registry_entry.device_id if registry_entry is not None else None
        if registry_entry is not None:
            registry.async_remove(entity.entity_id)

        # Depending on the device-grouping option (see device_grouping.py),
        # this device may be per-command, shared across many commands
        # (split-by-type), or the permanent hub device itself (unified) --
        # look it up by the entity's own device_id rather than assuming an
        # identifier, and never remove the hub device. HA never auto-
        # removes a device just because its entities are gone, so without
        # this a deleted command (or the last entity of a shared device)
        # leaves a permanent zero-entity ghost behind. Safe to run from
        # button.py/switch.py/select.py's setup independently: whichever
        # runs last is the one that actually finds zero entities left.
        if device_id is None:
            return
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        if device is None or (DOMAIN, entry.entry_id) in device.identifiers:
            return
        if not er.async_entries_for_device(registry, device.id, include_disabled_entities=True):
            device_registry.async_remove_device(device.id)

    for command in coordinator.data.values():
        _add_new(command)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_ADDED, _add_new))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_COMMAND_REMOVED, _remove))
