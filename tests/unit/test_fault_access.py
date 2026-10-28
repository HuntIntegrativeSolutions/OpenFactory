"""Fault API access (PLAN §6.4, ruled 2026-10-27): open on loopback; bound beyond loopback it
needs the configured token, and with no token it is off and ``run`` logs a one-line notice."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.config import load_settings
from openfactory.core.loader import load
from openfactory.server.app import FAULT_TOKEN_HEADER, create_app, fault_api_notice, is_loopback
from openfactory.server.runtime import Runtime

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"
JAM = {"id": "CYL_01", "kind": "jammed"}


def client(bind: str, token: str | None = None) -> TestClient:
    app = create_app(Runtime(load(SORTER)), web_dist=None, bind=bind, fault_token=token)
    return TestClient(app)


def calls(http: TestClient, headers: dict[str, str] | None = None) -> list[int]:
    """Status of GET, POST and DELETE on the fault API."""
    return [
        http.get("/api/fault", headers=headers).status_code,
        http.post("/api/fault", json=JAM, headers=headers).status_code,
        http.delete("/api/fault/CYL_01", headers=headers).status_code,
    ]


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_loopback_is_open_with_or_without_a_token(bind):
    assert is_loopback(bind)
    assert calls(client(bind)) == [200, 200, 200]
    assert calls(client(bind, token="s3cret")) == [200, 200, 200]  # loopback stays open
    assert fault_api_notice(bind, None) is None


def test_beyond_loopback_without_a_token_the_fault_api_is_off():
    http = client("0.0.0.0")
    assert calls(http) == [403, 403, 403]
    assert "fault API off" in http.get("/api/fault").json()["detail"]
    assert http.get("/api/state").status_code == 200  # the rest of the API is unaffected
    notice = fault_api_notice("0.0.0.0", None)
    print("notice:", notice)
    assert notice is not None and "\n" not in notice and "--fault-token" in notice
    assert not is_loopback("0.0.0.0") and not is_loopback("192.0.2.7")
    assert not is_loopback("plant-hmi.example")


def test_beyond_loopback_a_wrong_or_missing_token_is_rejected():
    http = client("0.0.0.0", token="s3cret")
    assert calls(http) == [401, 401, 401]
    assert calls(http, {FAULT_TOKEN_HEADER: "s3cre"}) == [401, 401, 401]
    assert calls(http, {FAULT_TOKEN_HEADER: "wrong-token"}) == [401, 401, 401]
    assert "s3cret" not in http.get("/api/fault").text  # the token is never echoed
    assert fault_api_notice("0.0.0.0", "s3cret") is None


def test_beyond_loopback_the_right_token_is_accepted():
    http = client("0.0.0.0", token="s3cret")
    assert calls(http, {FAULT_TOKEN_HEADER: "s3cret"}) == [200, 200, 200]


def test_fault_token_comes_from_the_server_table_local_file_winning(tmp_path):
    assert load_settings(tmp_path).server.fault_token is None
    (tmp_path / "openfactory.toml").write_text('[server]\nfault_token = "shared"\n')
    assert load_settings(tmp_path).server.fault_token == "shared"
    (tmp_path / "openfactory.local.toml").write_text('[server]\nfault_token = "mine"\n')
    assert load_settings(tmp_path).server.fault_token == "mine"
    (tmp_path / "openfactory.local.toml").write_text('[server]\nfault_token = ""\n')
    with pytest.raises(ValueError, match="at least 1 character"):
        load_settings(tmp_path)
