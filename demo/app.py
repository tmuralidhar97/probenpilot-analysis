import math

import requests
import streamlit as st

MOONRAKER_URL = "http://192.168.8.117:7125"
STATUS_TIMEOUT = 3
MOVEMENT_TIMEOUT = 30
Z_HOMING_TIMEOUT = 420  # Allow the full upward search at the configured 1 mm/s.
SYRINGE_SPEED = 3.0  # Motor revolutions per second with the syringe test config.
SYRINGE_ACCEL = 1.0  # Motor revolutions per second squared.
SYRINGE_STEPPER = "MANUAL_STEPPER STEPPER=syringe"
SYRINGE_MOTION = f"SPEED={SYRINGE_SPEED:g} ACCEL={SYRINGE_ACCEL:g}"
SAMPLE_Z_SPEED = 1.0  # mm/s

# Conservative demo limits, not verified hardware safety limits.
LIMITS = {
    "X": (0.0, 355.0),
    "Y": (0.0, 364.0),
    "Z": (-5.0, 347.0),
}

st.set_page_config(
    page_title="ProbenPilot | SV08 Controls",
    page_icon=":material/science:",
    layout="wide",
)


def get_printer_status(*, include_syringe_config: bool = False):
    objects = {
        "toolhead": "position,homed_axes,axis_minimum,axis_maximum",
        "print_stats": "state",
        "webhooks": "state",
    }
    if include_syringe_config:
        objects["configfile"] = "settings"

    response = requests.get(
        f"{MOONRAKER_URL}/printer/objects/query",
        params=objects,
        timeout=STATUS_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or "error" in result:
        raise RuntimeError(f"Moonraker status error: {result}")
    status = result["result"]["status"]
    toolhead = status["toolhead"]
    position = toolhead["position"]
    if not isinstance(position, list) or len(position) < 3:
        raise ValueError("Missing printer coordinates.")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in position[:3]
    ):
        raise ValueError("Printer coordinates must be finite numbers.")
    if not isinstance(toolhead["homed_axes"], str):
        raise TypeError("Invalid homing status.")
    if not isinstance(status["print_stats"]["state"], str):
        raise TypeError("Invalid print-job status.")
    if not isinstance(status["webhooks"]["state"], str):
        raise TypeError("Invalid Klipper status.")
    return status


def z_travel_limits(toolhead):
    """Use the intersection of the demo bounds and Klipper's Z travel range."""
    demo_min, demo_max = LIMITS["Z"]
    minimum = toolhead.get("axis_minimum", [0, 0, demo_min])
    maximum = toolhead.get("axis_maximum", [0, 0, demo_max])
    if any(
        not isinstance(bound, list) or len(bound) < 3 for bound in (minimum, maximum)
    ):
        raise ValueError("Klipper returned invalid Z travel limits.")
    klipper_min, klipper_max = minimum[2], maximum[2]
    if any(
        isinstance(bound, bool)
        or not isinstance(bound, (int, float))
        or not math.isfinite(bound)
        for bound in (klipper_min, klipper_max)
    ):
        raise ValueError("Klipper returned invalid Z travel limits.")
    z_min, z_max = max(demo_min, klipper_min), min(demo_max, klipper_max)
    if z_min >= z_max:
        raise ValueError("Klipper and the demo have no shared Z travel range.")
    return z_min, z_max


def send_gcode(command: str, *, timeout: float = MOVEMENT_TIMEOUT):
    """Send G-code through Moonraker."""
    response = requests.post(
        f"{MOONRAKER_URL}/printer/gcode/script",
        json={"script": command},
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()

    if not isinstance(result, dict) or result.get("result") != "ok":
        raise RuntimeError(f"Moonraker did not confirm the command: {result}")

    return result


def home_axis(axis: str):
    """Run the printer's homing override for the requested axes."""
    if axis not in {"ALL", "X", "Y", "Z"}:
        raise ValueError("Select Home all, X, Y, or Z.")
    current_status = get_printer_status()
    if current_status["webhooks"]["state"] != "ready":
        raise RuntimeError("Klipper must be ready before homing.")
    if current_status["print_stats"]["state"] != "standby":
        raise RuntimeError("Printer must be in standby before homing.")

    homes_z = axis in {"ALL", "Z"}
    timeout = Z_HOMING_TIMEOUT if homes_z else MOVEMENT_TIMEOUT
    command = "G28" if axis == "ALL" else f"G28 {axis}"
    send_gcode(f"{command}\nM400", timeout=timeout)
    verified = get_printer_status()
    if verified["webhooks"]["state"] != "ready":
        raise RuntimeError("Klipper is no longer ready after homing.")
    expected_axes = "xyz" if homes_z else axis.lower()
    if not all(
        expected in verified["toolhead"]["homed_axes"] for expected in expected_axes
    ):
        raise RuntimeError(
            f"Klipper did not confirm homing for {expected_axes.upper()}."
        )


def move_diagonal(distance: float, *, speed: float):
    """Decrease X and Y together, validating both targets before movement."""
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("Diagonal distance must be a positive finite number.")
    if not math.isfinite(speed) or not 0 < speed <= 10:
        raise ValueError("X/Y speed must be greater than 0 and at most 10 mm/s.")
    current_status = get_printer_status()
    if current_status["webhooks"]["state"] != "ready":
        raise RuntimeError("Klipper must be ready before movement.")
    if current_status["print_stats"]["state"] != "standby":
        raise RuntimeError("Printer must be in standby.")
    toolhead = current_status["toolhead"]
    if not all(axis in toolhead["homed_axes"] for axis in "xy"):
        raise RuntimeError("X and Y must both be homed before diagonal movement.")
    x, y = toolhead["position"][:2]

    targets = {"X": x - distance, "Y": y - distance}
    for axis, target in targets.items():
        minimum, maximum = LIMITS[axis]
        if not math.isfinite(target) or not minimum <= target <= maximum:
            raise RuntimeError(f"{axis} target exceeds demo limits.")

    send_gcode(f"G90\nG1 X{targets['X']:.3f} Y{targets['Y']:.3f} F{speed * 60:g}\nM400")


def move_to_xy(x: float, y: float, *, speed: float):
    """Move to absolute X/Y coordinates while keeping Z unchanged."""
    for axis, target in {"X": x, "Y": y}.items():
        minimum, maximum = LIMITS[axis]
        if not math.isfinite(target) or not minimum <= target <= maximum:
            raise ValueError(
                f"{axis} target must be between {minimum:g} and {maximum:g} mm."
            )
    if not math.isfinite(speed) or not 0 < speed <= 10:
        raise ValueError("X/Y speed must be greater than 0 and at most 10 mm/s.")
    current_status = get_printer_status()
    if current_status["webhooks"]["state"] != "ready":
        raise RuntimeError("Klipper must be ready before movement.")
    if current_status["print_stats"]["state"] != "standby":
        raise RuntimeError("Printer must be in standby.")
    if not all(axis in current_status["toolhead"]["homed_axes"] for axis in "xy"):
        raise RuntimeError("X and Y must both be homed before movement.")

    current_x, current_y = current_status["toolhead"]["position"][:2]
    # Allow longer requests for long moves at the selected speed.
    timeout = max(
        MOVEMENT_TIMEOUT, math.hypot(x - current_x, y - current_y) / speed + 10
    )
    send_gcode(f"G90\nG1 X{x:.3f} Y{y:.3f} F{speed * 60:g}\nM400", timeout=timeout)
    verified = get_printer_status()
    if verified["webhooks"]["state"] != "ready" or not all(
        axis in verified["toolhead"]["homed_axes"] for axis in "xy"
    ):
        raise RuntimeError("Printer readiness or X/Y homing was lost after movement.")
    actual_x, actual_y = verified["toolhead"]["position"][:2]
    if abs(actual_x - x) > 0.5 or abs(actual_y - y) > 0.5:
        raise RuntimeError("Klipper's reported X/Y position does not match the target.")


def get_syringe_status():
    status = get_printer_status(include_syringe_config=True)
    if (
        status["webhooks"]["state"] != "ready"
        or status["print_stats"]["state"] != "standby"
    ):
        raise RuntimeError("Klipper must be ready and the printer in standby.")

    syringe = status["configfile"]["settings"].get("manual_stepper syringe")
    if syringe is None:
        raise RuntimeError(
            "Klipper is missing the [manual_stepper syringe] configuration."
        )
    if syringe.get("rotation_distance") != 1 or syringe.get("gear_ratio"):
        raise RuntimeError(
            "Syringe controls require the test scaling of one unit per motor revolution."
        )
    return status, syringe


def jog_plunger(distance: float):
    """Move the syringe by a small signed distance from its current position."""
    _, syringe = get_syringe_status()
    if any(
        syringe.get(limit) is not None for limit in ("position_min", "position_max")
    ):
        raise RuntimeError(
            "Relative jogs re-zero the syringe coordinate and require the test config "
            "without absolute syringe position limits."
        )

    script = "\n".join(
        (
            "M400",
            f"{SYRINGE_STEPPER} ENABLE=1",
            f"{SYRINGE_STEPPER} SET_POSITION=0",
            f"{SYRINGE_STEPPER} MOVE={distance:.3f} {SYRINGE_MOTION}",
            "M400",
        )
    )
    send_gcode(script)


def collect_sample(plunger_start: float, draw_stroke: float, sample_z: float):
    """Run the syringe and Z moves in order from a measured setup."""
    plunger_start = round(plunger_start, 3)
    draw_stroke = round(draw_stroke, 3)
    if not all(
        math.isfinite(value) for value in (plunger_start, draw_stroke, sample_z)
    ):
        raise ValueError("Enter finite positions and a finite draw stroke.")
    if draw_stroke == 0:
        raise ValueError("Enter a nonzero draw stroke.")
    if (plunger_start < 0 < draw_stroke) or (draw_stroke < 0 < plunger_start):
        raise ValueError(
            "The starting position and draw stroke must be on the same side of bottom."
        )

    status, _ = get_syringe_status()
    toolhead = status["toolhead"]
    if not all(axis in toolhead["homed_axes"] for axis in "xyz"):
        raise RuntimeError("Home all axes before collecting a sample.")

    start_z = toolhead["position"][2]
    descent = round(start_z - sample_z, 3)
    z_min, z_max = z_travel_limits(toolhead)
    if not z_min <= start_z - descent < start_z <= z_max:
        raise ValueError(
            f"Set a submerged Z below the current Z and within {z_min:g} to {z_max:g} mm."
        )

    script = "\n".join(
        (
            "M400",
            f"{SYRINGE_STEPPER} ENABLE=1",
            f"{SYRINGE_STEPPER} SET_POSITION={plunger_start:.3f}",
            f"{SYRINGE_STEPPER} MOVE=0 {SYRINGE_MOTION}",
            "M400",
            "SAVE_GCODE_STATE NAME=probenpilot_collect",
            "M220 S100",
            "G91",
            f"G1 Z-{descent:.3f} F{SAMPLE_Z_SPEED * 60:g}",
            "M400",
            f"{SYRINGE_STEPPER} MOVE={draw_stroke:.3f} {SYRINGE_MOTION}",
            "M400",
            f"RESTORE_GCODE_STATE NAME=probenpilot_collect MOVE=1 MOVE_SPEED={SAMPLE_Z_SPEED:g}",
            "M400",
        )
    )
    travel_time = (abs(plunger_start) + abs(draw_stroke)) / SYRINGE_SPEED
    # Allow for acceleration and deceleration on both plunger moves.
    travel_time += 2 * SYRINGE_SPEED / SYRINGE_ACCEL
    travel_time += 2 * descent / SAMPLE_Z_SPEED
    if not math.isfinite(travel_time):
        raise ValueError("The requested travel is too large.")
    send_gcode(script, timeout=travel_time + MOVEMENT_TIMEOUT)


st.title("ProbenPilot", anchor=False)
st.caption("Sovol SV08 control panel")

if "homing_result" in st.session_state:
    st.success(st.session_state.pop("homing_result"))
if "movement_result" in st.session_state:
    st.success(st.session_state.pop("movement_result"))

z_min, z_max = LIMITS["Z"]
try:
    status = get_printer_status()
    z_min, z_max = z_travel_limits(status["toolhead"])
except (
    requests.RequestException,
    RuntimeError,
    KeyError,
    TypeError,
    ValueError,
    OverflowError,
) as exc:
    status = None
    st.error(f"Printer unavailable: {exc}")

connected = status is not None
homed_axes = status["toolhead"]["homed_axes"] if status is not None else ""

if status is not None:
    columns = st.columns(3)

    for column, axis, coordinate in zip(
        columns, ("X", "Y", "Z"), status["toolhead"]["position"]
    ):
        value = f"{coordinate:.2f} mm" if axis.lower() in homed_axes else "Not homed"
        column.metric(f"{axis} position", value, border=True)

with st.container(
    horizontal=True,
    horizontal_alignment="distribute",
    vertical_alignment="center",
    border=True,
):
    with st.container(horizontal=True, gap="xsmall", vertical_alignment="center"):
        if status is not None:
            st.badge("Moonraker connected", icon=":material/link:", color="green")
            st.badge(status["print_stats"]["state"].capitalize(), color="gray")
        else:
            st.badge("Moonraker unavailable", icon=":material/link_off:", color="red")
        st.caption(f"Homed axes · {homed_axes.upper() or 'None'}")

    with st.container(horizontal=True, vertical_alignment="center"):
        enable_motion = st.checkbox(
            "Enable live movement",
            value=False,
            disabled=not connected,
        )
        if st.button("Refresh Position", icon=":material/refresh:"):
            st.rerun()

sample_column, gantry_column = st.columns([1.15, 1], gap="medium")
sample_panel = sample_column.container(border=True)
syringe_panel = sample_column.container(border=True)
tip_panel = sample_column.container(border=True)
homing_panel = gantry_column.container(border=True)
motion_panel = gantry_column.container(border=True)
coordinate_panel = gantry_column.container(border=True)

standby_controls_enabled = (
    status is not None
    and enable_motion
    and status["webhooks"]["state"] == "ready"
    and status["print_stats"]["state"] == "standby"
)
tip_panel.subheader("Pipette tip", anchor=False)
if (
    tip_panel.button(
        "Eject pipette tip",
        key="eject_pipette",
        icon=":material/eject:",
        help="Available with live movement enabled and the printer in standby.",
        disabled=not standby_controls_enabled,
    )
    and standby_controls_enabled
):
    try:
        send_gcode("EJECT_PIPETTE")
    except requests.Timeout:
        tip_panel.error(
            "Ejection request timed out. Check the ejector and Mainsail "
            "before trying again."
        )
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        tip_panel.error(f"Pipette tip ejection failed: {exc}")
    else:
        tip_panel.success("Moonraker acknowledged EJECT_PIPETTE.")

homing_panel.subheader("Homing", anchor=False)
homing_panel.caption(
    "Home all runs the full homing sequence configured in Klipper. "
    "The demo also recognizes homing done in Mainsail."
)
confirm_homing = homing_panel.checkbox(
    "I have checked the homing path is clear and can stop the machine.",
    key="confirm_homing",
    disabled=not (connected and enable_motion),
)

homing_enabled = connected and enable_motion and confirm_homing
for column, axis in zip(homing_panel.columns(4, gap="xsmall"), ("ALL", "X", "Y", "Z")):
    label = "Home all" if axis == "ALL" else f"Home {axis}"
    with column:
        if (
            st.button(
                label,
                key=f"home_{axis.lower()}",
                width="stretch",
                disabled=not homing_enabled,
            )
            and homing_enabled
        ):
            try:
                home_axis(axis)
            except requests.Timeout:
                st.error(
                    "Homing request timed out. The printer may still be moving. "
                    "Check Mainsail and refresh the status before trying again."
                )
            except (
                requests.RequestException,
                RuntimeError,
                KeyError,
                TypeError,
                ValueError,
                OverflowError,
            ) as exc:
                st.error(f"{label} failed: {exc}")
            else:
                homed = "X, Y, and Z" if axis == "ALL" else axis
                st.session_state.homing_result = f"{homed} homing confirmed by Klipper."
                st.rerun()

sample_panel.subheader("Sample collection", anchor=False)
sample_panel.caption(
    "Home all axes, then position the tip above the sample at a clear starting height. "
    "The syringe has no bottom sensor in the test config. Enter measured values "
    "for your setup before each run."
)
sample_enabled = standby_controls_enabled and all(axis in homed_axes for axis in "xyz")
with sample_panel.form(
    "sample_collection", clear_on_submit=True, enter_to_submit=False, border=False
):
    plunger_start = st.number_input(
        "Current plunger position from bottom (signed motor revolutions)",
        value=None,
        step=0.001,
        format="%.3f",
        help="Use 0 if the plunger is already at bottom. Use a negative value if retracting moves in the negative direction.",
        key="sample_plunger_start",
    )
    draw_stroke = st.number_input(
        "Plunger draw stroke from bottom (signed motor revolutions)",
        value=None,
        step=0.001,
        format="%.3f",
        help="Use the sign that retracts the plunger. Enter a measured stroke within its travel.",
        key="sample_draw_stroke",
    )
    sample_z = st.number_input(
        "Submerged tip Z (mm)",
        min_value=float(z_min),
        max_value=float(z_max),
        value=None,
        step=0.1,
        format="%.3f",
        help="Enter the Z position where the tip is in the liquid and clears the container bottom.",
        key="sample_z",
    )
    confirm_sample = st.checkbox(
        "I verified the plunger position and stroke, tip alignment, and Z clearance.",
        key="confirm_sample",
    )
    collect_clicked = st.form_submit_button(
        "Collect sample",
        key="collect_sample",
        type="primary",
        icon=":material/science:",
        width="stretch",
        disabled=not sample_enabled,
    )

if collect_clicked and sample_enabled:
    if (
        not confirm_sample
        or plunger_start is None
        or draw_stroke is None
        or sample_z is None
    ):
        sample_panel.error("Enter all three measured values and confirm the setup.")
    else:
        try:
            with sample_panel, st.spinner("Collecting sample…"):
                collect_sample(plunger_start, draw_stroke, sample_z)
        except requests.Timeout:
            sample_panel.error(
                "Collection timed out. The sequence may still be running. "
                "Check Mainsail and the syringe and gantry positions before trying again."
            )
        except (
            requests.RequestException,
            RuntimeError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
        ) as exc:
            sample_panel.error(f"Collection stopped: {exc}")
        else:
            st.session_state.movement_result = "Klipper completed the collection sequence and returned to the starting Z."
            st.rerun()

syringe_panel.subheader("Syringe jog", anchor=False)
syringe_panel.caption(
    "Move only the plunger. Check that it has room to travel, and select the motor "
    "direction that retracts it."
)
jog_step_column, jog_direction_column = syringe_panel.columns(2, gap="small")
plunger_step = jog_step_column.selectbox(
    "Plunger step (motor rev)", (0.025, 0.05, 0.1, 0.5, 5, 30, 50), key="plunger_step"
)
retract_sign = jog_direction_column.selectbox(
    "Plunger up direction",
    (1, -1),
    index=None,
    format_func=lambda sign: "Positive (+)" if sign == 1 else "Negative (−)",
    key="plunger_up_direction",
)
plunger_enabled = standby_controls_enabled and retract_sign is not None
plunger_buttons = syringe_panel.columns(2, gap="xsmall")
for column, (direction_name, direction) in zip(
    plunger_buttons, (("down", -1), ("up", 1))
):
    clicked = column.button(
        f"Plunger {direction_name}",
        key=f"plunger_{direction_name}",
        width="stretch",
        disabled=not plunger_enabled,
    )
    if clicked and standby_controls_enabled and retract_sign is not None:
        try:
            jog_plunger(direction * retract_sign * plunger_step)
        except requests.Timeout:
            syringe_panel.error(
                "Plunger jog timed out. Check the syringe position before trying again."
            )
        except (
            requests.RequestException,
            RuntimeError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
        ) as exc:
            syringe_panel.error(f"Plunger jog failed: {exc}")
        else:
            syringe_panel.success("Klipper acknowledged the plunger jog.")

motion_panel.subheader("Manual movement", anchor=False)
if connected:
    motion_panel.caption(f"Z travel range · {z_min:g} to {z_max:g} mm")
distance_column, xy_speed_column, z_distance_column, z_speed_column = (
    motion_panel.columns(4, gap="small")
)

step = distance_column.selectbox(
    "Step size (mm)",
    [0.1, 1.0, 5.0, 10.0],
    index=1,
)
xy_speed = xy_speed_column.selectbox(
    "X/Y speed (mm/s)",
    [1.0, 5.0, 10.0, 20.0],
    index=2,
    key="xy_speed",
)
z_jog_distance = z_distance_column.selectbox(
    "Z jog distance (mm)",
    [5, 10, 25, 50, 100],
    index=2,
    key="z_jog_distance",
)
z_jog_speed = z_speed_column.selectbox(
    "Z jog speed (mm/s)",
    [1.0, 2.5, 5.0, 10.0, 20.0],
    index=1,
    key="z_jog_speed",
)

jog_controls = motion_panel.container(gap="xsmall")
coordinate_panel.subheader("Move to coordinates", anchor=False)
coordinate_panel.caption(
    "Enter absolute X/Y coordinates. Check that the path is clear. "
    "Limits are X 0–355 mm and Y 0–364 mm."
)
target_columns = coordinate_panel.columns(2, gap="small")
with target_columns[0]:
    target_x = st.number_input(
        "Target X (mm)",
        min_value=LIMITS["X"][0],
        max_value=LIMITS["X"][1],
        value=175.0,
        step=1.0,
        format="%.3f",
        key="target_x",
    )
with target_columns[1]:
    target_y = st.number_input(
        "Target Y (mm)",
        min_value=LIMITS["Y"][0],
        max_value=LIMITS["Y"][1],
        value=175.0,
        step=1.0,
        format="%.3f",
        key="target_y",
    )
xy_movement_enabled = (
    connected and enable_motion and all(axis in homed_axes for axis in "xy")
)
if (
    coordinate_panel.button(
        "Move to X/Y",
        key="move_to_xy",
        width="stretch",
        disabled=not xy_movement_enabled,
    )
    and xy_movement_enabled
):
    try:
        move_to_xy(target_x, target_y, speed=xy_speed)
    except requests.Timeout:
        coordinate_panel.error(
            "Movement request timed out. The printer may still be moving. "
            "Refresh the status before trying again."
        )
    except (
        requests.RequestException,
        RuntimeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:
        coordinate_panel.error(f"X/Y movement failed: {exc}")
    else:
        st.session_state.movement_result = (
            f"Klipper reports X={target_x:.3f} mm, Y={target_y:.3f} mm."
        )
        st.rerun()

if (
    motion_panel.button(
        "Diagonal X− / Y−",
        key="diagonal_left",
        help="Decrease X and Y together by the selected step size.",
        width="stretch",
        disabled=not xy_movement_enabled,
    )
    and xy_movement_enabled
):
    try:
        move_diagonal(step, speed=xy_speed)
    except requests.Timeout:
        motion_panel.error(
            "Movement request timed out. The printer may still be moving. "
            "Refresh the status before trying again."
        )
    except (
        requests.RequestException,
        RuntimeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:
        motion_panel.error(f"Diagonal movement failed: {exc}")
    else:
        st.session_state.movement_result = (
            f"Live command acknowledged for X −{step:g} mm, Y −{step:g} mm."
        )
        st.rerun()

for axis, large_step in (("X", 100), ("Y", 100), ("Z", z_jog_distance)):
    columns = jog_controls.columns(4, gap="xsmall")
    jog_enabled = connected and enable_motion
    if axis == "Z":
        jog_enabled = standby_controls_enabled and "z" in homed_axes

    jogs = (
        (f"{axis} -{large_step}", -large_step, f"Jog {axis} by -{large_step} mm."),
        (f"{axis} -", -step, "Jog by the selected step size."),
        (f"{axis} +", step, "Jog by the selected step size."),
        (f"{axis} +{large_step}", large_step, f"Jog {axis} by +{large_step} mm."),
    )
    for column, (label, distance, help_text) in zip(columns, jogs):
        with column:
            clicked = st.button(
                label,
                help=help_text,
                width="stretch",
                disabled=not jog_enabled,
            )

            if clicked and jog_enabled:
                try:
                    # Read fresh status immediately before movement.
                    current_status = get_printer_status()
                    current_toolhead = current_status["toolhead"]

                    if current_status["webhooks"]["state"] != "ready":
                        raise RuntimeError("Klipper must be ready before movement.")
                    if current_status["print_stats"]["state"] != "standby":
                        raise RuntimeError("Printer must be in standby.")

                    if axis.lower() not in current_toolhead["homed_axes"]:
                        raise RuntimeError(f"{axis} must be homed first.")

                    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]
                    current = current_toolhead["position"][axis_index]
                    target = current + distance

                    if axis == "Z":
                        minimum, maximum = z_travel_limits(current_toolhead)
                        target = round(target, 3)
                    else:
                        minimum, maximum = LIMITS[axis]

                    if not minimum <= target <= maximum:
                        raise RuntimeError(
                            f"{axis} target exceeds travel limits ({minimum:g} to {maximum:g} mm)."
                        )

                    # Absolute positioning avoids leaking G91 state.
                    # Only the requested axis changes.
                    speed = z_jog_speed if axis == "Z" else xy_speed
                    command = f"G90\nG1 {axis}{target:.3f} F{speed * 60:g}\nM400"
                    timeout = max(MOVEMENT_TIMEOUT, abs(distance) / speed + 10)

                    send_gcode(command, timeout=timeout)
                    st.success(
                        "Command acknowledged. Refresh the position "
                        "to verify the actual movement."
                    )
                    st.rerun()

                except requests.Timeout:
                    st.error(
                        "Jog request timed out. The printer may still be moving. "
                        "Check Mainsail and refresh the position before trying again."
                    )
                except (
                    requests.RequestException,
                    RuntimeError,
                    KeyError,
                    TypeError,
                    ValueError,
                    OverflowError,
                ) as exc:
                    st.error(f"Movement failed: {exc}")
