import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Moonraker runs on the printer's Linux computer.
    # Port 7125 is the default Moonraker HTTP port.
    moonraker_url: str = os.environ.get(
        "MOONRAKER_URL",
        "http://192.168.8.135:7125",
    )

    # Maximum time, in seconds, for status requests.
    request_timeout: float = 5.0

    # Movement can take longer than a status request.
    movement_timeout: float = 60.0

    # Conservative SOFTWARE-only X/Y workspace.
    # These are example commissioning limits, not
    # physically calibrated safe boundaries.
    min_x: float = 250.0
    max_x: float = 320.0
    min_y: float = 250.0
    max_y: float = 320.0

    # Movement speed in millimeters per minute.
    # F300 = 5 millimeters per second.
    feedrate: int = 300
