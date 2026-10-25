"""PLAN 1.10: `openfactory run` in real time, with the viewer server and Modbus up together."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from pymodbus.client import ModbusTcpClient

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_run_serves_viewer_and_modbus_then_stops_after_duration(tmp_path):
    scene = tmp_path / EXAMPLE.name
    scene.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    modbus_port, web_port = free_port(), free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "openfactory",
            "run",
            str(scene),
            "--duration",
            "3",
            "--port",
            str(modbus_port),
            "--web-port",
            str(web_port),
            "--force",
            "CONV_01_RUN_DO=1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        status = None
        deadline = time.monotonic() + 15
        while status is None:
            assert proc.poll() is None, proc.stdout.read()
            assert time.monotonic() < deadline, "server did not come up"
            try:
                status = httpx.get(f"http://127.0.0.1:{web_port}/api/status").json()
            except httpx.TransportError:
                time.sleep(0.1)
        assert status["scene"] == "sorting-by-height" and status["running"]
        client = ModbusTcpClient("127.0.0.1", port=modbus_port)
        assert client.connect()
        try:
            assert client.read_coils(0, count=1, device_id=1).bits[0] is True  # forced CO:0
            time.sleep(0.1)
            assert client.read_discrete_inputs(0, count=1, device_id=1).bits[0] is True
        finally:
            client.close()
        out, _ = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
    print(out)
    assert proc.returncode == 0
    assert "Modbus register map (float32, zero-based addresses):" in out
    assert f"viewer: http://127.0.0.1:{web_port}/" in out
    assert "ran 150 ticks (3.00 s simulated)" in out
