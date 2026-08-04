from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CommandRecord
from .const import DOMAIN
from .coordinator import IrRfHubCoordinator


class IrRfHubCommandEntity(CoordinatorEntity[IrRfHubCoordinator]):
    """Base for the button/switch pair on one command. One HA Device per
    Command (not per physical ESPHome device) -- users reason about "TV
    Power," not about which ESP32 happens to route it.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: IrRfHubCoordinator, command_id: str) -> None:
        super().__init__(coordinator)
        self._command_id = command_id

    @property
    def _command(self) -> CommandRecord | None:
        return self.coordinator.data.get(self._command_id)

    @property
    def available(self) -> bool:
        return super().available and self._command is not None

    @property
    def device_info(self) -> DeviceInfo:
        command = self._command
        name = command.name if command else self._command_id
        model = "IR Command" if (command is None or command.type == "ir") else "RF Command"
        return DeviceInfo(
            identifiers={(DOMAIN, self._command_id)},
            name=name,
            manufacturer="IR/RF Command Hub",
            model=model,
            via_device=(DOMAIN, self.coordinator.entry.entry_id),
        )
