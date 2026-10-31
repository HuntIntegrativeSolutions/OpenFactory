"""Connection settings from ``openfactory.toml`` (and the git-ignored ``openfactory.local.toml``).

Behavior spec
-------------
Connection settings never live in scene files. :func:`load_settings` reads ``openfactory.toml``
and then ``openfactory.local.toml`` from a directory (the current directory by default); keys in
the local file win, table by table. Missing files or keys fall back to built-in defaults (every
server on ``127.0.0.1``: Modbus 5020, OPC UA 4840, web 8000). Unknown keys are errors, like in
scenes.

:func:`resolve_endpoint` applies the full precedence for one server: CLI flag, then the scene's
optional ``plc.bind`` / ``plc.port`` override, then the settings files, then the defaults. The scene
schema already restricts ``plc.bind`` to loopback addresses (PLAN §6.4), so only the CLI flag or the
operator's own settings files can open a server to the network.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openfactory.core.schema import PlcConfig

SETTINGS_FILE = "openfactory.toml"
LOCAL_SETTINGS_FILE = "openfactory.local.toml"


#: The OpenFactory OPC UA server's namespace URI (PLAN §6.3); Ignition paths use it, not the index.
OPCUA_NAMESPACE = "urn:openfactory:sim"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Endpoint(_Strict):
    bind: str = "127.0.0.1"
    port: int = Field(ge=1, le=65535)


class Connection(_Strict):
    protocol: str
    path: str


class ServerSettings(_Strict):
    #: Token ``/api/fault`` requires when the web server is bound beyond loopback (PLAN §6.4).
    #: Put it in the git-ignored ``openfactory.local.toml``, never in the committed file.
    fault_token: str | None = Field(default=None, min_length=1)


class Settings(_Strict):
    modbus: Endpoint = Endpoint(port=5020)
    opcua: Endpoint = Endpoint(port=4840)
    web: Endpoint = Endpoint(port=8000)
    server: ServerSettings = ServerSettings()
    connections: dict[str, Connection] = {}


_DEFAULTS = Settings()


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(directory: str | Path | None = None) -> Settings:
    """Read ``openfactory.toml`` then ``openfactory.local.toml`` from ``directory``."""
    root = Path.cwd() if directory is None else Path(directory)
    data: dict[str, Any] = _DEFAULTS.model_dump()
    for name in (SETTINGS_FILE, LOCAL_SETTINGS_FILE):
        path = root / name
        if path.is_file():
            with path.open("rb") as fh:
                data = _merge(data, tomllib.load(fh))
    return Settings.model_validate(data)


def resolve_endpoint(
    configured: Endpoint,
    plc: PlcConfig | None = None,
    *,
    cli_bind: str | None = None,
    cli_port: int | None = None,
) -> Endpoint:
    """Apply CLI flag > scene override > settings file > default for one server endpoint."""
    bind = cli_bind or (plc.bind if plc and plc.bind else None) or configured.bind
    port = cli_port or (plc.port if plc and plc.port else None) or configured.port
    return Endpoint(bind=bind, port=port)
