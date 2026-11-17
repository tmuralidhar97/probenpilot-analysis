"""Validated X/Y motion for the modified Sovol SV08."""

import math

from .config import Config
from .moonraker_client import MoonrakerClient


class MotionError(Exception):
    """Raised when a movement is not permitted."""


class MotionController:
    """Controls X/Y movement after validating printer state."""

    def __init__(
        self,
        client: MoonrakerClient,
        config: Config,
    ) -> None:
        self.client = client
        self.config = config

    def validate_target(self, x: float, y: float) -> None:
        """Reject positions outside the approved workspace."""

        # Reject NaN and infinity, which are not valid coordinates.
        try:
            valid = all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                for value in (x, y)
            )
        except OverflowError as exc:
            raise MotionError("Coordinates must be finite numbers.") from exc
        if not valid:
            raise MotionError("Coordinates must be finite numbers.")

        if not self.config.min_x <= x <= self.config.max_x:
            raise MotionError(f"X={x} is outside the permitted X range.")

        if not self.config.min_y <= y <= self.config.max_y:
            raise MotionError(f"Y={y} is outside the permitted Y range.")

    def validate_machine(self) -> None:
        """Check readiness before allowing X/Y motion."""

        state = self.client.get_printer_state()

        if state != "ready":
            raise MotionError(f"Printer is not ready. Current state: {state}")

        status = self.client.get_motion_status()

        homed_axes = status["homed_axes"]

        # Both axes must have valid homing references.
        if "x" not in homed_axes or "y" not in homed_axes:
            raise MotionError("X and Y must be homed before movement.")

        # Never interfere with an active or paused print job.
        if status["job_state"] in {
            "printing",
            "paused",
            "error",
        }:
            raise MotionError(
                f"Movement blocked by print-job state: {status['job_state']}"
            )

    def move_to(
        self,
        x: float,
        y: float,
        *,
        execute: bool = False,
    ) -> str:
        """Preview or execute an absolute X/Y movement."""

        self.validate_target(x, y)
        self.validate_machine()

        # G90 selects absolute positioning.
        # G1 moves to the requested X/Y coordinates.
        # M400 waits for queued moves to complete.
        script = f"G90\nG1 X{x:.3f} Y{y:.3f} F{self.config.feedrate}\nM400"

        if not execute:
            # Preview mode does not send movement commands.
            return f"DRY RUN - no movement sent:\n{script}"

        self.client.execute_gcode(script)

        # Check Klipper's reported position after M400.
        # This is not an independent physical measurement.
        status = self.client.get_motion_status()
        position = status["position"]

        if abs(float(position[0]) - x) > 0.5 or abs(float(position[1]) - y) > 0.5:
            raise MotionError(
                "Movement command completed, but Klipper's "
                "reported position does not match the target."
            )

        return (
            f"Command completed. Klipper reports "
            f"X={position[0]:.3f}, Y={position[1]:.3f}"
        )

    def home_xy(self) -> None:
        """Home X and Y sequentially using the existing Klipper setup.

        This method is for supervised operation only.
        It never homes or moves the Z axis.
        """

        # Homing requires Klipper to be operational.
        state = self.client.get_printer_state()

        if state != "ready":
            raise MotionError(f"Cannot home axes. Printer state: {state}")

        # Read the current printer status.
        status = self.client.get_motion_status()

        # Do not home during an active or paused print job.
        if status["job_state"] != "standby":
            raise MotionError("Cannot home X/Y unless print-job state is standby.")

        homed_axes = status["homed_axes"]

        # Only home the axes that actually need homing.
        # Keep X and Y commands separate because the
        # modified printer has a custom homing_override.
        if "x" not in homed_axes:
            self.client.execute_gcode("G28 X\nM400")

        # Re-read the status after X homing.
        status = self.client.get_motion_status()

        if "x" not in status["homed_axes"]:
            raise MotionError("X homing was not confirmed.")

        if "y" not in status["homed_axes"]:
            self.client.execute_gcode("G28 Y\nM400")

        # Verify both axes before reporting success.
        status = self.client.get_motion_status()

        if not all(axis in status["homed_axes"] for axis in "xy"):
            raise MotionError("X/Y homing was not confirmed.")
