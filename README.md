# probenpilot-analysis
Python-based motion control and automation for a modified Sovol SV08 printer to a water analysis system.

## Current capabilities

- Connect to Moonraker over HTTP
- Read Klipper printer state
- Read X/Y homing and position information
- Preview X/Y movement commands
- Execute restricted X/Y moves with operator confirmation

## Current limitations

- Z movement is disabled in this software.
- Syringe actuator control is not implemented.
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
