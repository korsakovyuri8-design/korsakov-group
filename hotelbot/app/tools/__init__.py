from app.tools.hotel_request import CreateHotelRequestTool
from app.tools.registry import ToolRegistry


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CreateHotelRequestTool())
    return registry
