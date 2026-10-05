from app.tools.actions import SubmitActionTool
from app.tools.hotel_request import CreateHotelRequestTool
from app.tools.registry import ToolRegistry


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SubmitActionTool())
    registry.register(CreateHotelRequestTool())
    return registry
