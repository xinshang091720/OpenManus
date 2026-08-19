"""HTTP client for the company Revit plugin running on the local machine."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.runtime_paths import runtime_paths


DEFAULT_REVIT_API_BASE_URL = "http://127.0.0.1:39521/api/RevitApi"
# The legacy IIS/HTTP.sys plugin is registered for the `localhost` host name
# and its route includes the double slash before `api`.  Do not canonicalize
# this URL to 127.0.0.1 or collapse that slash: both changes cause HTTP 400.
LEGACY_DEVELOPMENT_REVIT_API_BASE_URL = "http://localhost:5000//api/RevitApi"
_DEFAULT_TIMEOUT = object()
REVIT_OPERATION_TIMEOUT_SECONDS = 2 * 60 * 60


class RevitApiError(RuntimeError):
    """A connection, protocol, or Revit business error with endpoint context."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        http_status: int | None = None,
        plugin_code: int | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.http_status = http_status
        self.plugin_code = plugin_code
        details = []
        if endpoint:
            details.append(f"endpoint={endpoint}")
        if http_status is not None:
            details.append(f"http_status={http_status}")
        if plugin_code is not None:
            details.append(f"plugin_code={plugin_code}")
        suffix = f" ({', '.join(details)})" if details else ""
        super().__init__(f"{message}{suffix}")


def _normalize_api_base_url(url: str) -> str:
    """Strip trailing slashes so path joins work correctly."""
    return url.rstrip("/")


def configured_revit_api_base_url() -> str:
    """Read the local plugin endpoint without embedding deployment ports in code."""
    environment_value = os.environ.get("BEESYNC_REVIT_API_BASE_URL")
    if environment_value:
        return _normalize_api_base_url(environment_value)

    endpoint_file = runtime_paths.config_dir / "revit-plugin.json"
    if endpoint_file.exists():
        try:
            with endpoint_file.open(encoding="utf-8") as file:
                payload = json.load(file)
            value = payload.get("apiBaseUrl") if isinstance(payload, dict) else None
            if isinstance(value, str) and value.strip():
                return _normalize_api_base_url(value)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RevitApiError(
                f"Invalid Revit plugin endpoint configuration: {error}"
            ) from error
    # Preserve source-checkout compatibility with the existing Revit plugin.
    # Every packaged desktop release receives its configured 39521-style URL
    # from C# or ProgramData, so it never silently depends on port 5000.
    if not getattr(sys, "frozen", False):
        return _normalize_api_base_url(LEGACY_DEVELOPMENT_REVIT_API_BASE_URL)
    return _normalize_api_base_url(DEFAULT_REVIT_API_BASE_URL)


class RevitApiClient:
    """Calls the documented local Revit API and normalizes its response envelope."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = REVIT_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        raw_url = base_url or configured_revit_api_base_url()
        self.base_url = _normalize_api_base_url(raw_url)
        self.timeout_seconds = timeout_seconds

    async def plugin_status(self, timeout_seconds: float = 1.5) -> Dict[str, Any]:
        """Return plugin readiness without making health checks fail the Runtime."""
        try:
            response = await self._request("GET", "/Health", timeout_seconds=timeout_seconds)
        except RevitApiError as error:
            if error.http_status in {404, 405}:
                return {
                    "status": "unsupported",
                    "endpoint": self.base_url,
                    "message": str(error),
                }
            return {
                "status": "unavailable",
                "endpoint": self.base_url,
                "message": str(error),
            }
        return {
            "status": "ready",
            "endpoint": self.base_url,
            "plugin_version": response.get("pluginVersion") or response.get("version"),
            "revit_connected": bool(response.get("revitConnected", True)),
            "ready_for_requests": bool(response.get("readyForRequests", True)),
            "message": response.get("msg", "Revit plugin is ready"),
        }

    async def ensure_plugin_ready(self) -> Dict[str, Any]:
        """Preflight a Revit request when the deployed plugin provides /Health."""
        status = await self.plugin_status()
        if status["status"] == "ready":
            if not status["revit_connected"] or not status["ready_for_requests"]:
                raise RevitApiError(
                    status["message"], endpoint="/Health"
                )
            return status
        if os.environ.get("BEESYNC_REVIT_REQUIRE_HEALTH", "").lower() in {"1", "true", "yes"}:
            raise RevitApiError(status["message"], endpoint="/Health")
        # Compatibility mode allows the existing plugin to operate until its
        # C# health endpoint is released.  Connection errors still surface on
        # the requested operation with its precise endpoint.
        return status

    async def one_click_identifier(
        self, standard_id: str | int, marjor_name: str, *, wait_forever: bool = False
    ) -> Dict[str, Any]:
        return await self._post(
            "/OneClickIdentifier",
            {"standardId": standard_id, "marjorName": marjor_name},
            wait_forever=wait_forever,
        )

    async def open_revit_file(self, rvt_file_path: str, *, wait_forever: bool = False) -> Dict[str, Any]:
        return await self._post_operation("/OpenRevitFile", {"rvtFilePath": rvt_file_path}, wait_forever)

    async def get_level_ifc_ident(
        self, standard_id: str | int, *, wait_forever: bool = False
    ) -> Dict[str, Any]:
        return await self._post(
            "/GetLevelIfcIdent", {"standardId": standard_id}, wait_forever=wait_forever
        )

    async def get_ifc_ident(
        self, standard_id: str | int, marjor_name: str, *, wait_forever: bool = False
    ) -> Dict[str, Any]:
        return await self._post(
            "/GetIfcIdent",
            {"standardId": standard_id, "marjorName": marjor_name},
            wait_forever=wait_forever,
        )

    async def clear_parameters(self, *, wait_forever: bool = False) -> Dict[str, Any]:
        return await self._post("/ClearParameters", wait_forever=wait_forever)

    async def one_click_assignment(
        self,
        standard_id: str | int,
        data: list[Dict[str, Any]],
        *,
        wait_forever: bool = False,
    ) -> Dict[str, Any]:
        return await self._post(
            "/OneClickAssignment",
            {"standardId": standard_id, "data": data},
            wait_forever=wait_forever,
        )

    async def save_as(self, folder_path: str, *, wait_forever: bool = False) -> Dict[str, Any]:
        return await self._post_operation("/SaveAs", {"folderPath": folder_path}, wait_forever)

    async def dwg_revit_grid_data(self, dwg_file_path: str, *, wait_forever: bool = False) -> Dict[str, Any]:
        """Read one matching DWG/Revit grid line from the active Revit document."""
        return await self._post_operation("/DwgRevitGridData", {"dwgFilePath": dwg_file_path}, wait_forever)

    async def batch_create_rooms(self, *, wait_forever: bool = False) -> Dict[str, Any]:
        """Create rooms for closed areas in the active Revit document."""
        return await self._post_operation("/BatchCreateRooms", None, wait_forever)

    async def update_room_name(self, room_data: list[Dict[str, Any]], *, wait_forever: bool = False) -> Dict[str, Any]:
        """Apply an approved room-name update payload to the active Revit document."""
        return await self._post_operation("/UpdateRoomName", {"RoomData": room_data}, wait_forever)

    async def base_point_setting(self, data: Dict[str, Any], *, wait_forever: bool = False) -> Dict[str, Any]:
        """Apply extracted DWG survey/base-point data to the active Revit document."""
        return await self._post_operation("/BasePointSetting", data, wait_forever)

    async def export_ifc(self, ifc_file_path: str, *, wait_forever: bool = False) -> Dict[str, Any]:
        """Start IFC export for the active Revit document."""
        return await self._post_operation("/ExportIFC", {"IfcFilePath": ifc_file_path}, wait_forever)

    async def _post_operation(
        self, path: str, payload: Optional[Dict[str, Any]], wait_forever: bool
    ) -> Dict[str, Any]:
        if wait_forever:
            return await self._post(path, payload, wait_forever=True)
        return await self._post(path, payload)

    async def _post(
        self, path: str, payload: Optional[Dict[str, Any]] = None, *, wait_forever: bool = False
    ) -> Dict[str, Any]:
        # ``wait_forever`` is retained as a source-compatible name for older
        # callers.  Revit operations are deliberately bounded at two hours so
        # a lost plugin response cannot hold a conversation lock forever.
        return await self._request(
            "POST",
            path,
            payload,
            timeout_seconds=REVIT_OPERATION_TIMEOUT_SECONDS if wait_forever else _DEFAULT_TIMEOUT,
        )

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timeout_seconds: float | None | object = _DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds if timeout_seconds is _DEFAULT_TIMEOUT else timeout_seconds,
            ) as client:
                response = await client.request(
                    method, f"{self.base_url}{path}", json=payload
                )
        except httpx.HTTPError as error:
            detail = str(error) or error.__class__.__name__
            raise RevitApiError(
                f"Unable to call local Revit plugin: {detail}", endpoint=path
            ) from error

        try:
            body = response.json()
        except ValueError as error:
            raise RevitApiError(
                "Revit plugin returned a non-JSON response",
                endpoint=path,
                http_status=response.status_code,
            ) from error

        if not isinstance(body, dict):
            raise RevitApiError(
                "Revit plugin response must be a JSON object",
                endpoint=path,
                http_status=response.status_code,
            )
        if body.get("code") != 200:
            raise RevitApiError(
                body.get("msg") or "Revit operation failed",
                endpoint=path,
                http_status=response.status_code,
                plugin_code=body.get("code") if isinstance(body.get("code"), int) else None,
            )
        return body
