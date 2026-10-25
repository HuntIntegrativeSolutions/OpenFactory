"""openfactory.toml settings: defaults, local overrides, strict keys, and endpoint precedence."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openfactory.core.config import Endpoint, load_settings, resolve_endpoint
from openfactory.core.schema import PlcConfig

REPO = Path(__file__).resolve().parents[2]


def test_defaults_without_files(tmp_path):
    settings = load_settings(tmp_path)
    assert settings.modbus == Endpoint(bind="127.0.0.1", port=5020)
    assert settings.opcua.port == 4840 and settings.web.port == 8000
    assert settings.connections == {}


def test_repo_settings_file_matches_defaults():
    settings = load_settings(REPO)
    assert settings.modbus == Endpoint(bind="127.0.0.1", port=5020)
    assert settings.web == Endpoint(bind="127.0.0.1", port=8000)


def test_local_file_overrides_shared_file(tmp_path):
    (tmp_path / "openfactory.toml").write_text("[modbus]\nport = 1502\n")
    (tmp_path / "openfactory.local.toml").write_text(
        '[modbus]\nbind = "0.0.0.0"\n\n'
        '[connections.bench]\nprotocol = "logix"\npath = "192.0.2.10/1/0"\n'
    )
    settings = load_settings(tmp_path)
    assert settings.modbus == Endpoint(bind="0.0.0.0", port=1502)
    assert settings.connections["bench"].path == "192.0.2.10/1/0"


def test_unknown_key_is_an_error(tmp_path):
    (tmp_path / "openfactory.toml").write_text("[modbus]\nprot = 1502\n")
    with pytest.raises(ValidationError, match="Extra inputs"):
        load_settings(tmp_path)


def test_endpoint_precedence():
    configured = Endpoint(bind="127.0.0.1", port=5020)
    assert resolve_endpoint(configured, PlcConfig()) == configured
    scene = PlcConfig(port=1502)
    assert resolve_endpoint(configured, scene) == Endpoint(bind="127.0.0.1", port=1502)
    assert resolve_endpoint(configured, scene, cli_port=502).port == 502
    assert resolve_endpoint(configured, scene, cli_bind="0.0.0.0").bind == "0.0.0.0"
