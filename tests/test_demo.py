"""Check the live demo with mocked Moonraker requests."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("streamlit")
requests = pytest.importorskip("requests")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "demo" / "app.py"


def status_response(
    *, axes="xy", job="standby", state="ready", position=None, z_limits=None
):
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
    if z_limits is not None:
        z_min, z_max = z_limits
        response.json.return_value["result"]["status"]["toolhead"].update(
            axis_minimum=[0, 0, z_min], axis_maximum=[355, 364, z_max]
        )
    return response


def live_app():
    return AppTest.from_file(str(APP)).run()


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
        assert app.checkbox[0].disabled
        assert all(
            button.disabled
            for button in app.button
            if button.label != "Refresh Position"
        )
        get.side_effect = None
        get.return_value = response
        next(
            button for button in app.button if button.label == "Refresh Position"
        ).click().run()
    assert not app.exception
    assert not app.error
    assert any("Moonraker connected" in item.value for item in app.markdown)
    assert app.metric[0].value == "300.00 mm"


def test_eject_pipette_sends_one_macro_when_live_movement_is_enabled():
    with (
        patch("requests.get", return_value=status_response(axes="")) as get,
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        get.assert_called_once()
        assert not app.radio
        assert app.button(key="eject_pipette").disabled
        app.checkbox[0].check().run()
        assert not app.button(key="eject_pipette").disabled
        post.assert_not_called()
        app.button(key="eject_pipette").click().run()
        assert not app.exception
        assert not app.error
        assert any("acknowledged EJECT_PIPETTE" in alert.value for alert in app.success)

        get.return_value = status_response(job="printing")
        app.run()
        assert app.button(key="eject_pipette").disabled

    post.assert_called_once_with(
        "http://192.168.8.117:7125/printer/gcode/script",
        json={"script": "EJECT_PIPETTE"},
        timeout=30,
    )


@pytest.mark.parametrize(
    "start, stroke, sample_z, confirmed, syringe_config, error",
    [
        (0.5, 0.75, 15.0, True, {"rotation_distance": 1}, None),
        (-0.5, -0.75, 15.0, True, {"rotation_distance": 1}, None),
        (0.0, 0.75, 15.0, True, {"rotation_distance": 1}, None),
        (0.5, 0.75, 15.0, False, {"rotation_distance": 1}, "confirm the setup"),
        (0.5, 0.75, 20.0, True, {"rotation_distance": 1}, "below the current Z"),
        (0.5, -0.75, 15.0, True, {"rotation_distance": 1}, "same side of bottom"),
        (0.5, 0.75, 15.0, True, None, "missing the [manual_stepper syringe]"),
    ],
)
def test_sample_collection_sequence(
    start, stroke, sample_z, confirmed, syringe_config, error
):
    response = status_response(axes="xyz", position=[300, 301, 20.0, 0])
    response.json.return_value["result"]["status"]["configfile"] = {
        "settings": {"manual_stepper syringe": syringe_config}
    }
    with (
        patch("requests.get", return_value=response) as get,
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        assert app.button(key="collect_sample").disabled
        app.checkbox[0].check().run()
        assert not app.button(key="collect_sample").disabled

        app.number_input(key="sample_plunger_start").set_value(start)
        app.number_input(key="sample_draw_stroke").set_value(stroke)
        app.number_input(key="sample_z").set_value(sample_z)
        app.checkbox(key="confirm_sample").set_value(confirmed)
        app.button(key="collect_sample").click().run()

        assert not app.exception
        if error:
            assert error in app.error[0].value
            post.assert_not_called()
            return

        assert not app.error
        assert any(
            call.kwargs["params"].get("configfile") == "settings"
            for call in get.call_args_list
        )
        assert any(
            "completed the collection sequence" in msg.value for msg in app.success
        )
        post.assert_called_once()
        assert post.call_args.kwargs["json"]["script"].splitlines() == [
            "M400",
            "MANUAL_STEPPER STEPPER=syringe ENABLE=1",
            f"MANUAL_STEPPER STEPPER=syringe SET_POSITION={start:.3f}",
            "MANUAL_STEPPER STEPPER=syringe MOVE=0 SPEED=3 ACCEL=1",
            "M400",
            "SAVE_GCODE_STATE NAME=probenpilot_collect",
            "M220 S100",
            "G91",
            "G1 Z-5.000 F60",
            "M400",
            f"MANUAL_STEPPER STEPPER=syringe MOVE={stroke:.3f} SPEED=3 ACCEL=1",
            "M400",
            "RESTORE_GCODE_STATE NAME=probenpilot_collect MOVE=1 MOVE_SPEED=1",
            "M400",
        ]
        assert post.call_args.kwargs["timeout"] == pytest.approx(
            46 + (abs(start) + abs(stroke)) / 3
        )


@pytest.mark.parametrize(
    ("key", "retract_sign", "distance"),
    [
        ("plunger_up", 1, "0.050"),
        ("plunger_down", 1, "-0.050"),
        ("plunger_up", -1, "-0.050"),
        ("plunger_down", -1, "0.050"),
    ],
)
def test_plunger_jog_only_moves_syringe(key, retract_sign, distance):
    response = status_response(axes="")
    response.json.return_value["result"]["status"]["configfile"] = {
        "settings": {"manual_stepper syringe": {"rotation_distance": 1}}
    }
    with (
        patch("requests.get", return_value=response),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        assert app.button(key=key).disabled
        app.checkbox[0].check().run()
        assert app.button(key=key).disabled
        app.selectbox(key="plunger_up_direction").set_value(retract_sign).run()
        app.selectbox(key="plunger_step").set_value(0.05).run()
        app.button(key=key).click().run()

    assert not app.exception
    assert not app.error
    assert "Klipper acknowledged the plunger jog." in app.success[0].value
    post.assert_called_once_with(
        "http://192.168.8.117:7125/printer/gcode/script",
        json={
            "script": "\n".join(
                [
                    "M400",
                    "MANUAL_STEPPER STEPPER=syringe ENABLE=1",
                    "MANUAL_STEPPER STEPPER=syringe SET_POSITION=0",
                    f"MANUAL_STEPPER STEPPER=syringe MOVE={distance} SPEED=3 ACCEL=1",
                    "M400",
                ]
            )
        },
        timeout=30,
    )


def test_live_homing_requires_enable_and_confirmation():
    with (
        patch("requests.get", return_value=status_response()),
        patch("requests.post") as post,
    ):
        app = live_app()
        assert app.button(key="home_all").disabled
        assert app.button(key="home_x").disabled
        assert app.button(key="home_y").disabled
        assert app.button(key="home_z").disabled
        app.checkbox[0].check().run()
        assert app.button(key="home_all").disabled
        assert app.button(key="home_x").disabled
        app.checkbox(key="confirm_homing").check().run()
        assert not app.button(key="home_all").disabled
        assert not app.button(key="home_x").disabled
        assert not app.button(key="home_y").disabled
        assert not app.button(key="home_z").disabled
    post.assert_not_called()


@pytest.mark.parametrize(
    "axis, expected_axes, timeout",
    [("x", "x", 30), ("y", "y", 30), ("z", "xyz", 420), ("all", "xyz", 420)],
)
def test_homing_sends_only_requested_command_and_verifies(axis, expected_axes, timeout):
    with (
        patch("requests.get", return_value=status_response(axes="")) as get,
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}

        def complete_homing(*args, **kwargs):
            get.return_value = status_response(axes=expected_axes)
            return post.return_value

        post.side_effect = complete_homing
        app = live_app()
        arm_homing(app)
        app.button(key=f"home_{axis}").click().run()
    assert not app.exception
    assert not app.error
    assert any("homing confirmed" in alert.value for alert in app.success)
    command = "G28" if axis == "all" else f"G28 {axis.upper()}"
    post.assert_called_once_with(
        "http://192.168.8.117:7125/printer/gcode/script",
        json={"script": f"{command}\nM400"},
        timeout=timeout,
    )


def test_home_all_requires_klipper_to_confirm_every_axis():
    with (
        patch("requests.get", return_value=status_response(axes="xy")),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        arm_homing(app)
        app.button(key="home_all").click().run()

    assert not app.exception
    assert "Klipper did not confirm homing for XYZ" in app.error[0].value
    post.assert_called_once()


def test_z_controls_follow_klipper_homing_status():
    with (
        patch("requests.get", return_value=status_response(axes="xy")) as get,
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        arm_homing(app)
        assert app.metric[2].value == "Not homed"
        assert all(
            button.disabled for button in app.button if button.label in ("Z +", "Z -")
        )
        assert app.button(key="collect_sample").disabled
        get.return_value = status_response(axes="xyz", position=[300, 301, 10, 0])
        next(
            button for button in app.button if button.label == "Refresh Position"
        ).click().run()
        assert app.metric[2].value == "10.00 mm"
        assert all(
            not button.disabled
            for button in app.button
            if button.label in ("Z +", "Z -")
        )
        assert not app.button(key="collect_sample").disabled
        app.button(key="home_x").click().run()
        assert not next(
            button for button in app.button if button.label == "Z +"
        ).disabled
        get.return_value = status_response(axes="xy")
        app.run()
        assert next(button for button in app.button if button.label == "Z +").disabled
        assert app.button(key="collect_sample").disabled
    assert not app.exception
    assert not app.error
    post.assert_called_once()
    assert post.call_args.kwargs["json"] == {"script": "G28 X\nM400"}


def test_z_jog_checks_that_z_is_still_homed_before_sending():
    with (
        patch("requests.get", return_value=status_response(axes="xyz")) as get,
        patch("requests.post") as post,
    ):
        app = live_app()
        app.checkbox[0].check().run()
        get.side_effect = [status_response(axes="xyz"), status_response(axes="xy")]
        next(button for button in app.button if button.label == "Z -").click().run()

    assert not app.exception
    assert app.error[0].value == "Movement failed: Z must be homed first."
    post.assert_not_called()


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
        ("Z +", "G90\nG1 Z1.000 F150\nM400"),
        ("Z -", "G90\nG1 Z-1.000 F150\nM400"),
        ("Diagonal X− / Y−", "G90\nG1 X299.000 Y300.000 F300\nM400"),
    ],
)
def test_xy_speed_selection_leaves_z_jog_speed_independent(label, expected):
    with (
        patch("requests.get", return_value=status_response(axes="xyz")),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        assert app.selectbox(key="xy_speed").value == 10.0
        assert app.selectbox(key="z_jog_speed").value == 2.5
        app.selectbox(key="xy_speed").set_value(5.0).run()
        app.checkbox[0].check().run()
        next(button for button in app.button if button.label == label).click().run()
    assert not app.exception
    assert not app.error
    post.assert_called_once()
    assert post.call_args.kwargs["json"] == {"script": expected}


def test_editing_absolute_coordinates_does_not_send_movement():
    with (
        patch("requests.get", return_value=status_response()),
        patch("requests.post") as post,
    ):
        app = live_app()
        app.checkbox[0].check().run()
        app.number_input(key="target_x").set_value(250.0).run()
        app.number_input(key="target_y").set_value(260.0).run()
    assert not app.exception
    assert [metric.value for metric in app.metric[:2]] == ["300.00 mm", "301.00 mm"]
    post.assert_not_called()


@pytest.mark.parametrize(
    ("label", "move", "feedrate", "timeout"),
    [
        ("X -100", "X91.000", 60, 110),
        ("X +100", "X291.000", 60, 110),
        ("Y -100", "Y65.000", 60, 110),
        ("Y +100", "Y265.000", 60, 110),
        ("Z -25", "Z5.000", 300, 30),
        ("Z +25", "Z55.000", 300, 30),
    ],
)
def test_large_jog_buttons_use_default_distances(label, move, feedrate, timeout):
    with (
        patch(
            "requests.get",
            return_value=status_response(axes="xyz", position=[191, 165, 30, 0]),
        ),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        assert next(button for button in app.button if button.label == label).disabled
        app.selectbox(key="xy_speed").set_value(1.0).run()
        app.selectbox(key="z_jog_speed").set_value(5.0).run()
        app.checkbox[0].check().run()
        next(button for button in app.button if button.label == label).click().run()

    assert not app.exception
    assert not app.error
    post.assert_called_once_with(
        "http://192.168.8.117:7125/printer/gcode/script",
        json={"script": f"G90\nG1 {move} F{feedrate}\nM400"},
        timeout=timeout,
    )


@pytest.mark.parametrize(("label", "target"), [("Z -100", 50), ("Z +100", 250)])
def test_large_z_jogs_use_selected_distance(label, target):
    with (
        patch(
            "requests.get",
            return_value=status_response(axes="xyz", position=[191, 165, 150, 0]),
        ),
        patch("requests.post") as post,
    ):
        post.return_value.json.return_value = {"result": "ok"}
        app = live_app()
        distance = app.selectbox(key="z_jog_distance")
        assert distance.options == ["5", "10", "25", "50", "100"]
        assert distance.value == 25
        distance.set_value(100).run()
        app.checkbox[0].check().run()
        next(button for button in app.button if button.label == label).click().run()

    assert not app.exception
    assert not app.error
    post.assert_called_once_with(
        "http://192.168.8.117:7125/printer/gcode/script",
        json={"script": f"G90\nG1 Z{target:.3f} F150\nM400"},
        timeout=50,
    )


def test_large_jog_rejects_target_outside_limit_using_fresh_position():
    safe = status_response(axes="xyz", position=[191, 165, 210, 0], z_limits=(150, 340))
    near_limit = status_response(
        axes="xyz", position=[191, 165, 175, 0], z_limits=(150, 340)
    )
    with (
        patch("requests.get", return_value=safe) as get,
        patch("requests.post") as post,
    ):
        app = live_app()
        app.selectbox(key="z_jog_distance").set_value(50).run()
        app.checkbox[0].check().run()
        get.side_effect = [safe, near_limit]
        next(button for button in app.button if button.label == "Z -50").click().run()

    assert not app.exception
    assert "Z target exceeds travel limits (150 to 340 mm)" in app.error[0].value
    post.assert_not_called()


def test_sample_collection_rechecks_klipper_z_minimum_before_syringe_moves():
    previous = status_response(
        axes="xyz", position=[191, 165, 200, 0], z_limits=(100, 340)
    )
    current = status_response(
        axes="xyz", position=[191, 165, 200, 0], z_limits=(150, 340)
    )
    current.json.return_value["result"]["status"]["configfile"] = {
        "settings": {"manual_stepper syringe": {"rotation_distance": 1}}
    }
    with (
        patch("requests.get", return_value=previous) as get,
        patch("requests.post") as post,
    ):
        app = live_app()
        app.checkbox[0].check().run()
        assert app.number_input(key="sample_z").min == 100.0
        assert app.number_input(key="sample_z").max == 340.0
        app.number_input(key="sample_plunger_start").set_value(0.5)
        app.number_input(key="sample_draw_stroke").set_value(0.75)
        app.number_input(key="sample_z").set_value(140.0)
        app.checkbox(key="confirm_sample").check()
        get.side_effect = [previous, current]
        app.button(key="collect_sample").click().run()

    assert not app.exception
    assert "within 150 to 340 mm" in app.error[0].value
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
