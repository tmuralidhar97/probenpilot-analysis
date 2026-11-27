"""Unit tests for supervised X/Y homing."""

import pytest

from probenpilot_sv08.config import Config
from probenpilot_sv08.motion_controller import (
    MotionController,
    MotionError,
)


class FakeClient:
    """Simulate a printer without connecting to hardware."""

    def __init__(self) -> None:
        self.state = "ready"
        self.axes = ""
        self.job_state = "standby"
        self.commands: list[str] = []

    def get_printer_state(self) -> str:
        return self.state

    def get_motion_status(self) -> dict:
        return {
            "homed_axes": self.axes,
            "job_state": self.job_state,
            "position": [300.0, 300.0, 0.0, 0.0],
        }

    def execute_gcode(self, script: str) -> None:
        # Remember the command instead of moving motors.
        self.commands.append(script)

        # Simulate successful Klipper homing.
        if "G28 X" in script:
            self.axes += "x"

        if "G28 Y" in script:
            self.axes += "y"

        if "G28 Z" in script:
            self.axes = "xyz"


def test_homes_x_and_y_in_order():
    client = FakeClient()
    controller = MotionController(client, Config())

    controller.home_xy()

    assert client.commands == [
        "G28 X\nM400",
        "G28 Y\nM400",
    ]
    assert set(client.axes) == {"x", "y"}


def test_skips_homing_when_already_homed():
    client = FakeClient()
    client.axes = "xy"

    controller = MotionController(client, Config())
    controller.home_xy()

    assert client.commands == []


@pytest.mark.parametrize("axes", ["", "xyz"])
def test_home_z_runs_override_even_with_provisional_z_reference(axes):
    client = FakeClient()
    client.axes = axes
    controller = MotionController(client, Config())

    controller.home_z()

    assert client.commands == ["G28 Z\nM400"]
    assert client.axes == "xyz"


def test_home_z_requires_confirmation_of_all_axes(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(client, "execute_gcode", client.commands.append)
    client.axes = "z"

    with pytest.raises(MotionError, match="all three axes"):
        MotionController(client, Config()).home_z()

    assert client.commands == ["G28 Z\nM400"]


@pytest.mark.parametrize("home", ["home_xy", "home_z"])
def test_rejects_homing_during_printing(home):
    client = FakeClient()
    client.job_state = "printing"

    controller = MotionController(client, Config())

    with pytest.raises(MotionError, match="standby"):
        getattr(controller, home)()

    assert client.commands == []


@pytest.mark.parametrize("home", ["home_xy", "home_z"])
def test_rejects_homing_when_printer_not_ready(home):
    client = FakeClient()
    client.state = "error"

    controller = MotionController(client, Config())

    with pytest.raises(MotionError, match="Cannot home"):
        getattr(controller, home)()

    assert client.commands == []
