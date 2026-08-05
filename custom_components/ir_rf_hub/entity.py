from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CommandRecord
from .const import CONF_DEVICE_GROUPING, DEFAULT_DEVICE_GROUPING, MODE_SEPARATE
from .coordinator import IrRfHubCoordinator
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
