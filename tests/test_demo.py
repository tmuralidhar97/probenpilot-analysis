"""Check status errors and refresh recovery with a simulated Moonraker."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("streamlit")
requests = pytest.importorskip("requests")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "demo" / "app.py"


def status_response(*, axes="xy", job="standby", state="ready", position=None):
    response = Mock()
    response.json.return_value = {
        "result": {
            "status": {
                "toolhead": {
                    "position": [300, 301, 0, 0] if position is None else position,
                    "homed_axes": axes,
                },
                "print_stats": {"state": job},
                "webhooks": {"state": state},
            }
        }
    }
    return response


def live_app():
    app = AppTest.from_file(str(APP)).run()
    app.radio[0].set_value("Live").run()
    return app


def arm_homing(app):
    app.checkbox[0].check().run()
    app.checkbox(key="confirm_homing").check().run()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"result": None},
        {"result": {"status": {"toolhead": {"position": [None, 300, 0, 0]}}}},
        {
            "result": {
                "status": {
                    "toolhead": {
                        "position": [float("nan"), 300, 0, 0],
                        "homed_axes": "xy",
                    }
                }
            }
        },
    ],
)
def test_invalid_status_shows_error(payload):
    response = Mock()
    response.json.return_value = payload
    with patch("requests.get", return_value=response):
        app = live_app()
    assert not app.exception
    assert app.error
    assert not app.success
    assert not app.metric


@pytest.mark.parametrize("error", [requests.Timeout(), requests.ConnectionError()])
def test_connection_failure_can_recover_on_refresh(error):
    response = status_response()
    with patch("requests.get", side_effect=error) as get:
        app = live_app()
        assert not app.exception
        assert app.error
        get.side_effect = None
        get.return_value = response
        next(
            button for button in app.button if button.label == "Refresh Position"
        ).click().run()
    assert not app.exception
    assert not app.error
    assert app.success
    assert app.metric[0].value == "300.00 mm"


def test_simulated_homing_does_not_contact_hardware():
    with patch("requests.get") as get, patch("requests.post") as post:
        app = AppTest.from_file(str(APP)).run()
        app.button(key="home_x").click().run()
        assert app.metric[0].value == "0.00 mm"
        assert app.metric[1].value == "175.00 mm"
        app.button(key="home_y").click().run()
        assert app.metric[1].value == "0.00 mm"
    assert not app.exception
    get.assert_not_called()
    post.assert_not_called()


def test_live_homing_requires_enable_and_confirmation():
    with (
        patch("requests.get", return_value=status_response()),
        patch("requests.post") as post,
    ):
        app = live_app()
        assert app.button(key="home_x").disabled
        assert app.button(key="home_y").disabled
        app.checkbox[0].check().run()
        assert app.button(key="home_x").disabled
        app.checkbox(key="confirm_homing").check().run()
        assert not app.button(key="home_x").disabled
        assert not app.button(key="home_y").disabled
    post.assert_not_called()


@pytest.mark.parametrize("axis", ["x", "y"])
def test_homing_sends_only_requested_axis_and_verifies(axis):
    with (
        patch("requests.get", return_value=status_response(axes="")) as get,
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}

        def complete_homing(*args, **kwargs):
            get.return_value = status_response(axes=axis)
            return post.return_value

        post.side_effect = complete_homing
        app = live_app()
        arm_homing(app)
        app.button(key=f"home_{axis}").click().run()
    assert not app.exception
    assert not app.error
    assert any("homing confirmed" in alert.value for alert in app.success)
    post.assert_called_once_with(
        "http://192.168.8.117:7125/printer/gcode/script",
        json={"script": f"G28 {axis.upper()}\nM400"},
        timeout=30,
    )


@pytest.mark.parametrize(
    "blocked",
    [
        status_response(job="printing"),
        status_response(job="paused"),
        status_response(state="shutdown"),
        status_response(state="error"),
    ],
)
def test_homing_checks_fresh_readiness_before_sending(blocked):
    with (
        patch("requests.get", return_value=status_response()) as get,
        patch("requests.post") as post,
    ):
        app = live_app()
        arm_homing(app)
        get.side_effect = [status_response(), blocked]
        app.button(key="home_x").click().run()
    assert not app.exception
    assert app.error
    post.assert_not_called()


@pytest.mark.parametrize("result", [{"result": "ok"}, {"error": "Homing failed"}])
def test_homing_reports_unconfirmed_axis_or_command(result):
    with (
        patch("requests.get", return_value=status_response(axes="")),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = result
        app = live_app()
        arm_homing(app)
        app.button(key="home_x").click().run()
    assert not app.exception
    assert app.error
    assert not any("homing confirmed" in alert.value for alert in app.success)
    post.assert_called_once()


def test_homing_timeout_does_not_retry():
    with (
        patch("requests.get", return_value=status_response()),
        patch("requests.post", side_effect=requests.Timeout()) as post,
    ):
        app = live_app()
        arm_homing(app)
        app.button(key="home_y").click().run()
    assert not app.exception
    assert "may still be moving" in app.error[0].value
    post.assert_called_once()


def test_diagonal_simulation_moves_xy_together_without_hardware():
    with patch("requests.get") as get, patch("requests.post") as post:
        app = AppTest.from_file(str(APP)).run()
        app.selectbox[0].set_value(5.0).run()
        app.button(key="diagonal_left").click().run()
    assert not app.exception
    assert [metric.value for metric in app.metric] == [
        "170.00 mm",
        "170.00 mm",
        "0.00 mm",
    ]
    get.assert_not_called()
    post.assert_not_called()


def test_diagonal_live_uses_fresh_position_and_one_coordinated_command():
    with (
        patch("requests.get", return_value=status_response()) as get,
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        assert app.button(key="diagonal_left").disabled
        app.checkbox[0].check().run()
        # The snapshot and the pre-command status differ: use the latter.
        get.side_effect = [
            status_response(),
            status_response(position=[250, 260, 10, 0]),
            status_response(),
        ]
        app.button(key="diagonal_left").click().run()
    assert not app.exception
    assert not app.error
    post.assert_called_once_with(
        "http://192.168.8.117:7125/printer/gcode/script",
        json={"script": "G90\nG1 X249.000 Y259.000 F600\nM400"},
        timeout=30,
    )


@pytest.mark.parametrize(
    "blocked",
    [
        status_response(axes="x"),
        status_response(axes="y"),
        status_response(job="printing"),
        status_response(job="paused"),
        status_response(state="shutdown"),
        status_response(position=[0, 300, 0, 0]),
        status_response(position=[300, 0, 0, 0]),
    ],
)
def test_diagonal_rechecks_state_homing_and_both_limits(blocked):
    with (
        patch("requests.get", return_value=status_response()) as get,
        patch("requests.post") as post,
    ):
        app = live_app()
        app.checkbox[0].check().run()
        get.side_effect = [status_response(), blocked]
        app.button(key="diagonal_left").click().run()
    assert not app.exception
    assert app.error
    post.assert_not_called()


def test_diagonal_simulation_rejects_entire_move_at_boundary():
    with patch("requests.post") as post:
        app = AppTest.from_file(str(APP)).run()
        app.button(key="home_x").click().run()
        app.button(key="diagonal_left").click().run()
    assert not app.exception
    assert app.error
    assert [metric.value for metric in app.metric] == [
        "0.00 mm",
        "175.00 mm",
        "0.00 mm",
    ]
    post.assert_not_called()


def test_diagonal_timeout_does_not_retry():
    with (
        patch("requests.get", return_value=status_response()),
        patch("requests.post", side_effect=requests.Timeout()) as post,
    ):
        app = live_app()
        app.checkbox[0].check().run()
        app.button(key="diagonal_left").click().run()
    assert not app.exception
    assert "may still be moving" in app.error[0].value
    post.assert_called_once()


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("X +", "G90\nG1 X301.000 F300\nM400"),
        ("Y +", "G90\nG1 Y302.000 F300\nM400"),
        ("Z +", "G90\nG1 Z1.000 F60\nM400"),
        ("Diagonal X− / Y−", "G90\nG1 X299.000 Y300.000 F300\nM400"),
    ],
)
def test_speed_selection_applies_to_xy_and_preserves_z_speed(label, expected):
    with (
        patch("requests.get", return_value=status_response(axes="xyz")),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        assert app.selectbox(key="xy_speed").value == 10.0
        app.selectbox(key="xy_speed").set_value(5.0).run()
        app.checkbox[0].check().run()
        next(button for button in app.button if button.label == label).click().run()
    assert not app.exception
    assert not app.error
    assert post.call_args.kwargs["json"] == {"script": expected}


def test_absolute_coordinates_simulate_without_moving_on_input_change():
    with patch("requests.get") as get, patch("requests.post") as post:
        app = AppTest.from_file(str(APP)).run()
        app.number_input(key="target_x").set_value(250.0).run()
        app.number_input(key="target_y").set_value(260.0).run()
        assert app.metric[0].value == "175.00 mm"
        app.button(key="move_to_xy").click().run()
    assert not app.exception
    assert [metric.value for metric in app.metric] == [
        "250.00 mm",
        "260.00 mm",
        "0.00 mm",
    ]
    get.assert_not_called()
    post.assert_not_called()


def test_absolute_live_coordinates_and_long_move_timeout():
    with (
        patch("requests.get", return_value=status_response()) as get,
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        assert app.button(key="move_to_xy").disabled
        app.checkbox[0].check().run()
        app.number_input(key="target_x").set_value(10.0).run()
        app.number_input(key="target_y").set_value(20.0).run()
        app.selectbox(key="xy_speed").set_value(1.0).run()
        get.side_effect = [
            status_response(),
            status_response(),
            status_response(position=[10, 20, 0, 0]),
            status_response(position=[10, 20, 0, 0]),
        ]
        app.button(key="move_to_xy").click().run()
    assert not app.exception
    assert not app.error
    post.assert_called_once()
    assert post.call_args.kwargs["json"] == {
        "script": "G90\nG1 X10.000 Y20.000 F60\nM400"
    }
    assert post.call_args.kwargs["timeout"] > 400


@pytest.mark.parametrize(
    "blocked",
    [
        status_response(job="printing"),
        status_response(job="paused"),
        status_response(state="shutdown"),
        status_response(axes="x"),
        status_response(axes="y"),
    ],
)
def test_absolute_move_rechecks_fresh_status(blocked):
    with (
        patch("requests.get", return_value=status_response()) as get,
        patch("requests.post") as post,
    ):
        app = live_app()
        app.checkbox[0].check().run()
        get.side_effect = [status_response(), blocked]
        app.button(key="move_to_xy").click().run()
    assert not app.exception
    assert app.error
    post.assert_not_called()


def test_absolute_move_reports_position_mismatch():
    with (
        patch("requests.get", return_value=status_response()),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        app.checkbox[0].check().run()
        app.button(key="move_to_xy").click().run()
    assert not app.exception
    assert "does not match" in app.error[0].value
    post.assert_called_once()


def test_absolute_move_timeout_does_not_retry():
    with (
        patch("requests.get", return_value=status_response()),
        patch("requests.post", side_effect=requests.Timeout()) as post,
    ):
        app = live_app()
        app.checkbox[0].check().run()
        app.button(key="move_to_xy").click().run()
    assert not app.exception
    assert "may still be moving" in app.error[0].value
    post.assert_called_once()
