"""Exercise communication failures without contacting a printer."""

import io
import json
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from probenpilot_sv08.config import Config
from probenpilot_sv08.moonraker_client import MoonrakerClient, MoonrakerError


@pytest.mark.parametrize(
    "error",
    [
        URLError("Connection refused"),
        HTTPError("http://printer", 503, "Unavailable", {}, None),
        TimeoutError("Timed out"),
        ConnectionResetError("Connection reset"),
        IncompleteRead(b"partial"),
    ],
)
def test_communication_errors_preserve_cause(error):
    with (
        patch("probenpilot_sv08.moonraker_client.urlopen", side_effect=error),
        pytest.raises(MoonrakerError, match="Cannot communicate") as caught,
    ):
        MoonrakerClient(Config()).get_printer_state()
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("body", [b"not JSON", b"\xff"])
def test_invalid_json(body):
    with (
        patch(
            "probenpilot_sv08.moonraker_client.urlopen", return_value=io.BytesIO(body)
        ),
        pytest.raises(MoonrakerError, match="invalid JSON"),
    ):
        MoonrakerClient(Config()).get_printer_state()


@pytest.mark.parametrize(
    "result",
    [
        [],
        {},
        {"error": "Klipper unavailable"},
        {"result": None},
        {"result": {"state": None}},
        {"result": {"state": 1}},
    ],
)
def test_invalid_printer_response(result):
    with (
        patch(
            "probenpilot_sv08.moonraker_client.urlopen",
            return_value=io.BytesIO(json.dumps(result).encode()),
        ),
        pytest.raises(MoonrakerError),
    ):
        MoonrakerClient(Config()).get_printer_state()


@pytest.mark.parametrize(
    "position",
    [
        None,
        [],
        [300],
        ["300", 300],
        [None, 300],
        [True, 300],
        [float("nan"), 300],
        [300, float("inf")],
        [10**400, 300],
    ],
)
def test_invalid_positions_are_rejected(position):
    response = {
        "result": {
            "status": {
                "toolhead": {"homed_axes": "xy", "position": position},
                "print_stats": {"state": "standby"},
            }
        }
    }
    with (
        patch(
            "probenpilot_sv08.moonraker_client.urlopen",
            return_value=io.BytesIO(json.dumps(response).encode()),
        ),
        pytest.raises(MoonrakerError, match="invalid printer status"),
    ):
        MoonrakerClient(Config()).get_motion_status()


def test_invalid_url():
    with pytest.raises(MoonrakerError, match="Invalid Moonraker URL"):
        MoonrakerClient(Config(moonraker_url="invalid")).get_printer_state()


def test_valid_status():
    response = {
        "result": {
            "status": {
                "toolhead": {"homed_axes": "xy", "position": [300, 300, 0, 0]},
                "print_stats": {"state": "standby"},
            }
        }
    }
    with patch(
        "probenpilot_sv08.moonraker_client.urlopen",
        return_value=io.BytesIO(json.dumps(response).encode()),
    ):
        assert MoonrakerClient(Config()).get_motion_status() == {
            "homed_axes": "xy",
            "position": [300, 300, 0, 0],
            "job_state": "standby",
        }
