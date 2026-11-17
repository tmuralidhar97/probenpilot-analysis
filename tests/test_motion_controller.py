"""Tests for the X/Y motion safety rules."""

import pytest

from probenpilot_sv08.config import Config
from probenpilot_sv08.motion_controller import (
    MotionController,
    MotionError,
)


class FakeMoonrakerClient:
    """Fake printer API used only during automated tests."""

    def __init__(self) -> None:
        self.state = "ready"
        self.homed_axes = "xy"
        self.job_state = "standby"
        self.position = [300.0, 300.0, 0.0, 0.0]
        self.sent_scripts: list[str] = []

    def get_printer_state(self) -> str:
        return self.state

    def get_motion_status(self) -> dict:
        return {
            "homed_axes": self.homed_axes,
            "position": self.position,
            "job_state": self.job_state,
        }

    def execute_gcode(self, script: str) -> None:
        self.sent_scripts.append(script)


@pytest.fixture
def controller():
    """Provide an isolated controller for each test."""

    fake_client = FakeMoonrakerClient()
    config = Config()

    motion_controller = MotionController(
        fake_client,
        config,
    )

    return motion_controller, fake_client


def test_preview_does_not_move_hardware(controller):
    motion_controller, fake_client = controller

    result = motion_controller.move_to(300, 300)

    assert "DRY RUN" in result
    assert fake_client.sent_scripts == []


def test_rejects_unhomed_axes(controller):
    motion_controller, fake_client = controller

    fake_client.homed_axes = ""

    with pytest.raises(MotionError, match="must be homed"):
        motion_controller.move_to(300, 300)

    assert fake_client.sent_scripts == []


def test_rejects_out_of_bounds_target(controller):
    motion_controller, fake_client = controller

    with pytest.raises(MotionError, match="outside"):
        motion_controller.move_to(400, 300)

    assert fake_client.sent_scripts == []


def test_rejects_active_print_job(controller):
    motion_controller, fake_client = controller

    fake_client.job_state = "printing"

    with pytest.raises(MotionError, match="print-job state"):
        motion_controller.move_to(300, 300)

    assert fake_client.sent_scripts == []


def test_rejects_printer_not_ready(controller):
    motion_controller, fake_client = controller

    fake_client.state = "shutdown"

    with pytest.raises(MotionError, match="not ready"):
        motion_controller.move_to(300, 300)

    assert fake_client.sent_scripts == []


def test_execute_sends_expected_gcode(controller):
    motion_controller, fake_client = controller

    result = motion_controller.move_to(
        300,
        300,
        execute=True,
    )

    assert fake_client.sent_scripts == ["G90\nG1 X300.000 Y300.000 F300\nM400"]
    assert "Command completed" in result


@pytest.mark.parametrize(
    "value", [None, "300", True, float("nan"), float("inf"), 10**400]
)
def test_invalid_coordinates_do_not_contact_printer(controller, value, monkeypatch):
    motion_controller, fake_client = controller

    def unexpected_request():
        pytest.fail("Invalid coordinates must be rejected before contacting hardware")

    monkeypatch.setattr(fake_client, "get_printer_state", unexpected_request)
    with pytest.raises(MotionError, match="finite numbers"):
        motion_controller.move_to(value, 300, execute=True)
    assert fake_client.sent_scripts == []
