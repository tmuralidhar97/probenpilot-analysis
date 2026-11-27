# probenpilot-analysis
Python-based motion control and automation for a modified Sovol SV08 printer to a water analysis system.

## Current capabilities

- Connect to Moonraker over HTTP
- Read Klipper printer state
- Read Klipper's reported homed axes and positions
- Run supervised Z homing through the Klipper override
- Collect a liquid sample with the syringe and gantry from the Streamlit demo
- Preview X/Y movement commands
- Execute restricted X/Y moves with operator confirmation

## Current limitations

- The command-line `move` command accepts X/Y coordinates.
- The syringe test configuration has no bottom sensor or volume calibration.
- Motion boundaries are provisional and require validation.
- The system is not approved for unattended operation.

## Requirements

- Python 3.11 or newer
- Sovol SV08 running Klipper and Moonraker
- Network access to Moonraker
- X and Y homed before movement

## Installation

Create a virtual environment:

```powershell
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Install the project:

```powershell
python -m pip install -e ".[dev]"
```

## Usage

Read printer status:

```powershell
python -m probenpilot_sv08 status
```

To run the configured Z homing sequence, use **Home Z** in the Streamlit demo
or run:

```powershell
python -m probenpilot_sv08 home-z
```

The CLI requires you to type `HOME`. The demo requires live movement and homing
confirmation. `G28 Z` follows the active Klipper homing override. Check clearance
along the full path.

With the proximity-probe override, leave `set_position_z` unset so Klipper
reports Z as homed after probing. The demo uses Klipper's current homed axes, so
homing from Mainsail or the CLI also enables Z jogging after a position refresh.

Set the lowest allowed Z in the existing `[stepper_z]` section of `printer.cfg`
with `position_min`. Use a measured height that clears the fixture. Klipper
enforces that limit for normal moves. The demo reads Klipper's Z travel range
and applies its own bounds as well before jogging or collecting.

For **Collect sample**, enable live movement, home all axes, and jog the tip
above the sample to a clear starting height. Enter the measured current plunger
position and draw stroke as signed motor revolutions from the bottom position.
Use zero for the current position if the plunger is already at bottom. Use the
sign of the motor's retraction direction for the draw stroke. Enter the measured
submerged Z where the tip clears the container bottom. Confirm the setup and
click **Collect sample** to lower the plunger to its reference, dip the tip,
draw liquid, and return to the starting Z.

Use **Syringe jog** to test the plunger independently. Enable live movement,
check clearance, select the motor direction that retracts the plunger and a
small step in motor revolutions, then use **Plunger up** or **Plunger down**.
After jogging, measure the plunger position from bottom for **Collect sample**.

Preview movement:

```powershell
python -m probenpilot_sv08 move --x 300 --y 300
```

Execute movement after verification:

```powershell
python -m probenpilot_sv08 move --x 300 --y 300 --execute
```

The program requires the operator to type MOVE.

## Tests

```powershell
pytest -q
ruff check .
ruff format --check .
```

## Safety

Never run motion tests without confirming mechanical clearance,
a valid homing reference, and access to emergency-stop controls.

Current software boundaries are not calibrated safety limits.
Do not use for unattended automation.
