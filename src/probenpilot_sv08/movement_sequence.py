"""Supervised X/Y movement sequence with optional homing."""

import logging
import time

from .config import Config
from .moonraker_client import MoonrakerClient, MoonrakerError
from .motion_controller import MotionController, MotionError

logger = logging.getLogger(__name__)

# Fixed test positions in millimeters.
START_X = 300.0
START_Y = 300.0
TARGET_X = 310.0
TARGET_Y = 310.0

# Pause after the first movement finishes.
WAIT_SECONDS = 10


def confirm(message: str, expected: str) -> bool:
    """Require the operator to type an exact confirmation."""

    answer = input(f"{message} Type {expected}: ")
    return answer == expected


def main() -> int:
    """Run a supervised two-position movement test."""

    # Create the same configuration and client
    # used by our other application commands.
    config = Config()
    client = MoonrakerClient(config)
    controller = MotionController(client, config)

    try:
        # Read the current machine state.
        if client.get_printer_state() != "ready":
            raise MotionError("Klipper is not ready.")

        status = client.get_motion_status()
        homed_axes = status["homed_axes"]

        # Step 1: Home X/Y only when necessary.
        if not all(axis in homed_axes for axis in "xy"):
            logger.info("X/Y are not fully homed.")

            print("Homing will move the carriage toward its X/Y reference positions.")
            print("Check the entire homing path and keep the stop control accessible.")

            if not confirm(
                "Allow X/Y homing?",
                "HOME",
            ):
                print("Cancelled. No homing command sent.")
                return 0

            # Uses G28 X and G28 Y, never G28 Z.
            controller.home_xy()
            logger.info("X/Y homing confirmed.")

        # Verify the printer before positioning.
        controller.validate_machine()

        # Step 2: Prepare the sequence starting position.
        status = client.get_motion_status()
        position = status["position"]

        at_start = (
            abs(float(position[0]) - START_X) <= 0.5
            and abs(float(position[1]) - START_Y) <= 0.5
        )

        if not at_start:
            # Preview the starting-position command.
            print(controller.move_to(START_X, START_Y))

            if not confirm(
                "Move to the starting position?",
                "START",
            ):
                print("Cancelled before sequence positioning.")
                return 0

            controller.move_to(
                START_X,
                START_Y,
                execute=True,
            )

        # Step 3: Preview the complete movement sequence.
        print("\nPlanned sequence:")
        print(f"1. Move to X{TARGET_X} Y{TARGET_Y}")
        print(f"2. Wait {WAIT_SECONDS} seconds")
        print(f"3. Return to X{START_X} Y{START_Y}")

        # Explicit confirmation before the timed sequence.
        if not confirm(
            "Execute the timed sequence?",
            "MOVE",
        ):
            print("Sequence cancelled.")
            return 0

        # Step 4: First diagonal movement.
        logger.info("Moving to target position.")

        result = controller.move_to(
            TARGET_X,
            TARGET_Y,
            execute=True,
        )
        print(result)

        # Step 5: Wait after Klipper finishes the move.
        logger.info("Waiting %s seconds.", WAIT_SECONDS)
        time.sleep(WAIT_SECONDS)

        # Step 6: Return to the starting position.
        logger.info("Returning to starting position.")

        result = controller.move_to(
            START_X,
            START_Y,
            execute=True,
        )
        print(result)

        logger.info("Movement sequence completed.")
        return 0

    except (MoonrakerError, MotionError) as exc:
        logger.error("Sequence stopped: %s", exc)
        return 1

    except (KeyboardInterrupt, EOFError):
        logger.warning(
            "Operator interrupted the Python program. Already queued moves may continue."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
