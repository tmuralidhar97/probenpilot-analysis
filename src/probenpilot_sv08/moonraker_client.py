import json
import math
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config


class MoonrakerError(Exception):
    """Raised when communication with Moonraker fails."""


class MoonrakerClient:
    def __init__(self, config: Config) -> None:
        # Store the configuration for later requests.
        self.config = config

    def _request(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:

        url = self.config.moonraker_url.rstrip("/") + path

        # Requests with a JSON payload use POST.
        # Requests without a payload use GET.
        body = None
        headers = {"Accept": "application/json"}

        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        try:
            request = Request(
                url=url,
                data=body,
                headers=headers,
                method="POST" if body is not None else "GET",
            )
        except ValueError as exc:
            raise MoonrakerError(f"Invalid Moonraker URL: {url}") from exc

        try:
            with urlopen(
                request,
                timeout=self.config.request_timeout if timeout is None else timeout,
            ) as response:
                try:
                    result = json.load(response)
                except (ValueError, UnicodeError) as exc:
                    raise MoonrakerError("Moonraker returned invalid JSON.") from exc

        except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
            raise MoonrakerError(f"Cannot communicate with Moonraker: {exc}") from exc

        except ValueError as exc:
            raise MoonrakerError(f"Invalid Moonraker request: {exc}") from exc

        # A successful HTTP response can still contain
        # a Moonraker application-level error.
        if not isinstance(result, dict):
            raise MoonrakerError("Unexpected Moonraker response.")

        if "error" in result:
            raise MoonrakerError(f"Moonraker error: {result['error']}")

        if "result" not in result:
            raise MoonrakerError("Moonraker response has no result field.")

        return result

    def get_printer_state(self) -> str:
        """Read the printer's current state."""

        response = self._request("/printer/info")

        try:
            state = response["result"]["state"]
            if not isinstance(state, str) or not state:
                raise ValueError("Invalid printer state")
            return state
        except (KeyError, TypeError, ValueError) as exc:
            raise MoonrakerError("Printer state is missing or invalid.") from exc

    def get_motion_status(self) -> dict[str, Any]:
        """Read homing, position and print-job state."""

        response = self._request(
            "/printer/objects/query?toolhead=homed_axes,position&print_stats=state"
        )

        try:
            status = response["result"]["status"]
            toolhead = status["toolhead"]
            print_stats = status["print_stats"]

            axes = toolhead["homed_axes"]
            position = toolhead["position"]
            job_state = print_stats["state"]

            # homed_axes must be a string such as "xy" or "".
            if not isinstance(axes, str):
                raise TypeError("Invalid homing data")

            if not isinstance(position, list) or len(position) < 2:
                raise ValueError("Invalid position data")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in position
            ):
                raise ValueError("Position must contain finite numbers")

            # The print-job state must be a string.
            if not isinstance(job_state, str):
                raise TypeError("Invalid print-job state")

            return {
                "homed_axes": axes,
                "position": position,
                "job_state": job_state,
            }

        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MoonrakerError("Incomplete or invalid printer status.") from exc

    def execute_gcode(self, script: str) -> None:
        """Send an approved G-code script to Klipper."""

        response = self._request(
            "/printer/gcode/script",
            payload={"script": script},
            timeout=self.config.movement_timeout,
        )

        # Moonraker normally reports "ok" on success.
        if response["result"] != "ok":
            raise MoonrakerError(f"Unexpected G-code response: {response['result']}")
