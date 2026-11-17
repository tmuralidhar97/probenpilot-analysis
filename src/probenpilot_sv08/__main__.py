"""Command-line interface for the SV08 controller."""

import argparse
import logging
import sys

from .config import Config
from .moonraker_client import MoonrakerClient, MoonrakerError
from .motion_controller import MotionController, MotionError

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the command requested by the operator."""

    # Create a command-line argument parser.
    parser = argparse.ArgumentParser(description="ProbenPilot SV08 X/Y controller")

    # Support separate commands: status and move.
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "status",
        help="Read printer state without moving hardware",
    )

    move_parser = commands.add_parser(
        "move",
        help="Preview or execute an X/Y movement",
    )

    move_parser.add_argument(
        "--x",
        type=float,
        required=True,
        help="Target X coordinate in millimeters",
    )

    move_parser.add_argument(
        "--y",
        type=float,
        required=True,
        help="Target Y coordinate in millimeters",
    )

    move_parser.add_argument(
        "--execute",
        action="store_true",
        help="Allow movement after interactive confirmation",
    )

    args = parser.parse_args()

    # Create the settings and API client.
    config = Config()
    client = MoonrakerClient(config)
    controller = MotionController(client, config)

    try:
        if args.command == "status":
            state = client.get_printer_state()
            status = client.get_motion_status()

            print(f"Printer state: {state}")
            print(
                "Homed axes:",
                status["homed_axes"] or "none",
            )
            print("Reported position:", status["position"])
            print("Print-job state:", status["job_state"])
            return 0

        if args.command == "move":
            # Always show a preview first.
            preview = controller.move_to(args.x, args.y)
            print(preview)

            if not args.execute:
                print("Use --execute to request real movement.")
                return 0

            print(
                "\nVerify the carriage path is clear "
                "and the machine can be stopped safely."
            )

            # Require an explicit answer before motion.
            confirmation = input("Type MOVE to execute: ")

            if confirmation != "MOVE":
                print("Cancelled. No movement command sent.")
                return 0

            # Recheck printer state immediately before motion.
            result = controller.move_to(
                args.x,
                args.y,
                execute=True,
            )
            print(result)
            return 0

    except (MoonrakerError, MotionError) as exc:
        logger.error("%s", exc)
        return 1

    except (KeyboardInterrupt, EOFError):
        logger.warning("Cancelled or interrupted. Already queued moves may continue.")
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
