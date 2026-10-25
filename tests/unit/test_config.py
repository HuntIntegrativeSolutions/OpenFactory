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


# --- scene plc.bind is loopback only (PLAN §6.4) ------------------------------------------------


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.0.0.2", "127.255.255.254", "::1"])
def test_scene_bind_override_accepts_loopback(bind):
    configured = Endpoint(bind="127.0.0.1", port=5020)
    assert resolve_endpoint(configured, PlcConfig(bind=bind)).bind == bind


@pytest.mark.parametrize(
    "bind", ["0.0.0.0", "192.168.1.10", "10.0.0.5", "::", "fe80::1", "localhost", "my-host", ""]
)
def test_scene_bind_override_rejects_anything_else(bind):
    with pytest.raises(ValidationError, match="not a loopback address") as err:
        PlcConfig(bind=bind)
    assert "--bind" in str(err.value)  # the message names the CLI flag that does allow it


def test_scene_file_with_network_bind_fails_at_load(tmp_path):
    from openfactory.core.schema import load_scene

    example = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"
    text = example.read_text(encoding="utf-8").replace(
        "  role: server                  # server | client\n",
        "  role: server\n  bind: 0.0.0.0\n",
    )
    assert "bind: 0.0.0.0" in text
    path = tmp_path / "open.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError, match=r"plc\.bind '0\.0\.0\.0' is not a loopback.*--bind"):
        load_scene(path)


def test_cli_bind_still_opens_the_network():
    configured = Endpoint(bind="127.0.0.1", port=5020)
    plc = PlcConfig(bind="127.0.0.2")
    assert resolve_endpoint(configured, plc, cli_bind="0.0.0.0").bind == "0.0.0.0"
