from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CommandRecord
from .const import CONF_DEVICE_GROUPING, DEFAULT_DEVICE_GROUPING
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
    def available(self) -> bool:
        return super().available and self._command is not None

    @property
    def device_info(self) -> DeviceInfo:
        mode = self.coordinator.entry.options.get(CONF_DEVICE_GROUPING, DEFAULT_DEVICE_GROUPING)
        return device_info_for(
            self.coordinator.entry.entry_id, self._command_id, self._command, self._entity_kind, mode
        )
