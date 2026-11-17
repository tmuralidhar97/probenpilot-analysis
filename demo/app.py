import math

import requests
import streamlit as st

MOONRAKER_URL = "http://192.168.8.117:7125"
STATUS_TIMEOUT = 3
MOVEMENT_TIMEOUT = 30

# Conservative demo limits, not verified hardware safety limits.
LIMITS = {
    "X": (0.0, 355.0),
    "Y": (0.0, 364.0),
    "Z": (-5.0, 347.0),
}

st.set_page_config(
    page_title="ProbenPilot SV08 Demo",
    page_icon="🔬",
    layout="wide",
)

if "position" not in st.session_state:
    st.session_state.position = {
        "X": 175.0,
        "Y": 175.0,
        "Z": 0.0,
    }


def get_printer_status():
    response = requests.get(
        f"{MOONRAKER_URL}/printer/objects/query",
        params={
            "toolhead": "position,homed_axes",
            "print_stats": "state",
            "webhooks": "state",
        },
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
    """Home one requested axis using the printer's existing homing setup."""
    if axis not in {"X", "Y"}:
        raise ValueError("Only X and Y homing is supported here.")
    current_status = get_printer_status()
    if current_status["webhooks"]["state"] != "ready":
        raise RuntimeError("Klipper must be ready before homing.")
    if current_status["print_stats"]["state"] != "standby":
        raise RuntimeError("Printer must be in standby before homing.")

    send_gcode(f"G28 {axis}\nM400")
    verified = get_printer_status()
    if verified["webhooks"]["state"] != "ready":
        raise RuntimeError("Klipper is no longer ready after homing.")
    if axis.lower() not in verified["toolhead"]["homed_axes"]:
        raise RuntimeError(f"{axis} homing was not confirmed by Klipper.")


def move_simulated(axis: str, distance: float):
    current = st.session_state.position[axis]
    target = current + distance

    minimum, maximum = LIMITS[axis]

    if minimum <= target <= maximum:
        st.session_state.position[axis] = target
    else:
        st.error(f"{axis} exceeds demo limits.")


def move_diagonal(distance: float, *, live: bool, speed: float):
    """Decrease X and Y together, validating both targets before movement."""
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("Diagonal distance must be a positive finite number.")
    if not math.isfinite(speed) or not 0 < speed <= 10:
        raise ValueError("X/Y speed must be greater than 0 and at most 10 mm/s.")
    if live:
        current_status = get_printer_status()
        if current_status["webhooks"]["state"] != "ready":
            raise RuntimeError("Klipper must be ready before movement.")
        if current_status["print_stats"]["state"] != "standby":
            raise RuntimeError("Printer must be in standby.")
        toolhead = current_status["toolhead"]
        if not all(axis in toolhead["homed_axes"] for axis in "xy"):
            raise RuntimeError("X and Y must both be homed before diagonal movement.")
        x, y = toolhead["position"][:2]
    else:
        x = st.session_state.position["X"]
        y = st.session_state.position["Y"]

    targets = {"X": x - distance, "Y": y - distance}
    for axis, target in targets.items():
        minimum, maximum = LIMITS[axis]
        if not math.isfinite(target) or not minimum <= target <= maximum:
            raise RuntimeError(f"{axis} target exceeds demo limits.")

    if live:
        send_gcode(
            f"G90\nG1 X{targets['X']:.3f} Y{targets['Y']:.3f} F{speed * 60:g}\nM400"
        )
    else:
        st.session_state.position.update(targets)


def move_to_xy(x: float, y: float, *, live: bool, speed: float):
    """Move to absolute X/Y coordinates while keeping Z unchanged."""
    for axis, target in {"X": x, "Y": y}.items():
        minimum, maximum = LIMITS[axis]
        if not math.isfinite(target) or not minimum <= target <= maximum:
            raise ValueError(
                f"{axis} target must be between {minimum:g} and {maximum:g} mm."
            )
    if not math.isfinite(speed) or not 0 < speed <= 10:
        raise ValueError("X/Y speed must be greater than 0 and at most 10 mm/s.")
    if not live:
        st.session_state.position.update({"X": x, "Y": y})
        return

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


st.title("ProbenPilot — SV08 Demo")

if "homing_result" in st.session_state:
    st.success(st.session_state.pop("homing_result"))
if "movement_result" in st.session_state:
    st.success(st.session_state.pop("movement_result"))

mode = st.radio(
    "Operating mode",
    ["Simulation", "Live"],
    horizontal=True,
)

live = mode == "Live"
status = None
connected = False
homed_axes = ""

if live:
    try:
        status = get_printer_status()
        homed_axes = status["toolhead"]["homed_axes"]
        x, y, z = status["toolhead"]["position"][:3]

        position = {"X": x, "Y": y, "Z": z}
        connected = True

        st.success("Connected to Sovol SV08")

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
        position = None
else:
    position = st.session_state.position
    st.info("Simulation mode — no hardware movement.")

if position is not None:
    columns = st.columns(3)

    for column, axis in zip(columns, ("X", "Y", "Z")):
        is_homed = not live or axis.lower() in homed_axes

        column.metric(
            f"{axis} Position",
            f"{position[axis]:.2f} mm" if is_homed else "Not homed",
        )
st.divider()

if live:
    st.write("Homed axes:", homed_axes or "None")

    if status:
        st.write("Printer state:", status["print_stats"]["state"])

    enable_motion = st.checkbox(
        "Enable live movement",
        value=False,
        disabled=not connected,
    )
else:
    enable_motion = False

st.subheader("X/Y homing")
st.caption(
    "Homing uses your printer's existing Klipper configuration. A custom "
    "homing override may also move other axes. Demo movement limits do not "
    "restrict the homing path."
)
if live:
    confirm_homing = st.checkbox(
        "I have checked the homing path is clear and can stop the machine.",
        key="confirm_homing",
        disabled=not (connected and enable_motion),
    )
else:
    confirm_homing = False

for column, axis in zip(st.columns(2), ("X", "Y")):
    with column:
        if st.button(
            f"Home {axis}",
            key=f"home_{axis.lower()}",
            width="stretch",
            disabled=live and not (connected and enable_motion and confirm_homing),
        ):
            if not live:
                st.session_state.position[axis] = 0.0
                st.session_state.homing_result = f"Simulation: {axis} homed to 0 mm."
                st.rerun()
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
                st.error(f"{axis} homing failed: {exc}")
            else:
                st.session_state.homing_result = f"{axis} homing confirmed by Klipper."
                st.rerun()

st.subheader("Manual Movement")

step = st.selectbox(
    "Movement distance (mm)",
    [0.1, 1.0, 5.0, 10.0],
    index=1,
)
xy_speed = st.selectbox(
    "X/Y movement speed (mm/s)",
    [1.0, 5.0, 10.0],
    index=2,
    key="xy_speed",
)

st.subheader("Move to coordinates")
st.caption(
    "Enter absolute coordinates in millimeters. Both axes move together to the "
    "target at the selected X/Y speed; Z stays unchanged. Check the full path "
    "is clear. Configured limits: X 0–355 mm, Y 0–364 mm."
)
target_columns = st.columns(2)
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
if st.button(
    "Move to X/Y",
    key="move_to_xy",
    disabled=live
    and not (connected and enable_motion and all(axis in homed_axes for axis in "xy")),
):
    try:
        move_to_xy(target_x, target_y, live=live, speed=xy_speed)
    except requests.Timeout:
        st.error(
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
        st.error(f"X/Y movement failed: {exc}")
    else:
        st.session_state.movement_result = (
            f"{'Klipper reports' if live else 'Simulation'}: "
            f"X={target_x:.3f} mm, Y={target_y:.3f} mm."
        )
        st.rerun()

st.caption(
    "Diagonal toward the left side: decrease X and Y by the selected "
    "distance per click. Z stays at its current position. The configured limits "
    "are provisional; confirm clearance before live movement."
)
if st.button(
    "Diagonal X− / Y−",
    key="diagonal_left",
    disabled=live
    and not (connected and enable_motion and all(axis in homed_axes for axis in "xy")),
):
    try:
        move_diagonal(step, live=live, speed=xy_speed)
    except requests.Timeout:
        st.error(
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
        st.error(f"Diagonal movement failed: {exc}")
    else:
        st.session_state.movement_result = (
            f"{'Live command acknowledged' if live else 'Simulation'}: "
            f"X −{step:g} mm, Y −{step:g} mm."
        )
        st.rerun()

for axis in ("X", "Y", "Z"):
    st.write(f"**{axis} Axis**")
    negative, positive = st.columns(2)

    for column, direction in [
        (negative, -1),
        (positive, 1),
    ]:
        label = f"{axis} {'+' if direction > 0 else '-'}"

        with column:
            clicked = st.button(
                label,
                width="stretch",
                disabled=live and not (connected and enable_motion),
            )

            if clicked:
                distance = direction * step

                if not live:
                    move_simulated(axis, distance)
                    st.rerun()
                    continue

                try:
                    # Read fresh status immediately before movement.
                    current_status = get_printer_status()
                    current_toolhead = current_status["toolhead"]

                    if current_status["print_stats"]["state"] != "standby":
                        raise RuntimeError("Printer must be in standby.")

                    if axis.lower() not in current_toolhead["homed_axes"]:
                        raise RuntimeError(f"{axis} must be homed first.")

                    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]
                    current = current_toolhead["position"][axis_index]
                    target = current + distance

                    minimum, maximum = LIMITS[axis]

                    if not minimum <= target <= maximum:
                        raise RuntimeError(f"{axis} target exceeds demo limits.")

                    # Absolute positioning avoids leaking G91 state.
                    # Only the requested axis changes.
                    feedrate = xy_speed * 60 if axis in {"X", "Y"} else 60
                    command = f"G90\nG1 {axis}{target:.3f} F{feedrate:g}\nM400"

                    send_gcode(command)
                    st.success(
                        "Command acknowledged. Refresh the position "
                        "to verify the actual movement."
                    )
                    st.rerun()

                except (
                    requests.RequestException,
                    RuntimeError,
                    KeyError,
                    TypeError,
                    ValueError,
                    OverflowError,
                ) as exc:
                    st.error(f"Movement failed: {exc}")

st.divider()

if st.button("Refresh Position"):
    st.rerun()
