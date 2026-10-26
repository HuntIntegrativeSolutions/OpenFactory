"""Configure an OpenPLC v3 runtime for an OpenFactory scene through its web UI (PLAN task 1.14).

Usage::

    uv run python tools/openplc_configure.py --program examples/openplc/sorting-by-height.st \\
        --csv sorting-by-height-int16.csv --device-ip host.docker.internal [--device-port 5020] \\
        [--url http://127.0.0.1:8080] [--user openplc --password openplc] [--no-start]

It drives the same pages a person would use (docs/guides/openplc-quickstart.md), in headless
Chromium with Playwright:

1. Logs in, and stops the PLC if it is running (device changes apply on the next start).
2. Programs > Upload Program: uploads the Structured Text file, names it after the file, and waits
   until the compile log says "Compilation finished successfully!" (exit 1 on a compile error,
   with the log).
3. Slave Devices: deletes any device with the same name (``OpenFactory`` by default), then adds a
   "Generic Modbus TCP Device" at ``--device-ip``:``--device-port`` whose start address and size per
   table come from the ``modbus_address`` column of the exported I/O list CSV
   (``openfactory export SCENE --analog-encoding int16_scaled``): discrete inputs, coils, input
   registers, holding registers (write). Holding registers (read) are left at size 0.
4. Prints the Slave Devices table as OpenPLC shows it (the %IX/%QX/%IW/%QW locations it assigned).
5. Starts the PLC (unless ``--no-start``) and waits for the dashboard to say Running.

Training and demo use only; OpenFactory is not a safety-rated simulation.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any

#: OpenPLC form fields per Modbus table: (start field, size field).
FIELDS = {
    "DI": ("di_start", "di_size"),
    "CO": ("do_start", "do_size"),
    "IR": ("ai_start", "ai_size"),
    "HR": ("aow_start", "aow_size"),
}


def table_ranges(csv_path: Path) -> dict[str, tuple[int, int]]:
    """``{table: (start, size)}`` from the CSV's ``modbus_address`` column (``DI:4``, ``HR:0-1``).

    A table with no tags is ``(0, 0)``. The range spans the lowest to the highest address used, so
    holes (ADR-0004) are polled too; the simulator answers them with 0.
    """
    spans: dict[str, list[int]] = {t: [] for t in FIELDS}
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            table, _, addresses = row["modbus_address"].partition(":")
            lo, _, hi = addresses.partition("-")
            spans[table] += [int(lo), int(hi or lo)]
    return {t: (min(v), max(v) - min(v) + 1) if v else (0, 0) for t, v in spans.items()}


def login(page: Any, url: str, user: str, password: str) -> None:
    page.goto(f"{url}/login")
    page.fill("#username", user)
    page.fill("#password", password)
    page.click("form.login-form button")
    page.wait_for_load_state()
    if "/login" in page.url:
        raise RuntimeError("OpenPLC login failed")


def stop_plc(page: Any, url: str) -> None:
    page.goto(f"{url}/stop_plc")
    page.wait_for_load_state()


def upload_program(page: Any, url: str, program: Path, timeout: float = 300.0) -> str:
    """Upload and compile a program; return the compile log."""
    page.goto(f"{url}/programs")
    page.set_input_files("input[name=file]", str(program))
    page.click("input[type=submit][value='Upload Program']")
    page.wait_for_load_state()
    page.fill("#prog_name", program.stem)
    page.fill(
        "#prog_descr", f"OpenFactory example {program.name}, uploaded by {Path(__file__).name}"
    )
    page.click("input[type=submit][value='Upload program']")
    page.wait_for_load_state()
    deadline = time.monotonic() + timeout
    while True:
        log = page.request.get(f"{url}/compilation-logs").text()
        if "Compilation finished successfully" in log:
            return log
        if "finished with errors" in log or "error" in log.strip().splitlines()[-1:][0].lower():
            raise RuntimeError(f"compile failed:\n{log}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"compile did not finish:\n{log}")
        time.sleep(1)


def delete_device(page: Any, url: str, name: str) -> bool:
    page.goto(f"{url}/modbus")
    row = page.locator("tr", has_text=name).first
    if row.count() == 0:
        return False
    row.click()
    page.wait_for_load_state()
    page.click("text=Delete device")
    page.wait_for_load_state()
    return True


def add_device(
    page: Any, url: str, name: str, ip: str, port: int, ranges: dict[str, tuple[int, int]]
) -> None:
    page.goto(f"{url}/add-modbus-device")
    page.fill("#dev_name", name)
    page.select_option("#dev_protocol", "TCP")
    page.fill("#dev_id", "1")
    page.fill("#dev_ip", ip)
    page.fill("#dev_port", str(port))
    for table, (start_field, size_field) in FIELDS.items():
        start, size = ranges[table]
        page.fill(f"input[name={start_field}]", str(start))
        page.fill(f"input[name={size_field}]", str(size))
    page.fill("input[name=aor_start]", "0")
    page.fill("input[name=aor_size]", "0")
    page.click("input[type=submit][value='Save device']")
    page.wait_for_load_state()


def device_table(page: Any, url: str) -> str:
    page.goto(f"{url}/modbus")
    return "\n".join(
        " | ".join(c.strip() for c in row.locator("th, td").all_inner_texts())
        for row in page.locator("table tr").all()
    )


def start_plc(page: Any, url: str, timeout: float = 30.0) -> str:
    page.goto(f"{url}/start_plc")
    deadline = time.monotonic() + timeout
    while True:
        page.goto(f"{url}/dashboard")
        status = page.locator("text=Status:").first.inner_text()
        if "Running" in page.inner_text("body"):
            return "Running"
        if time.monotonic() > deadline:
            raise TimeoutError(f"PLC did not start: {status}")
        time.sleep(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--program", type=Path, required=True, help="Structured Text file")
    parser.add_argument("--csv", type=Path, required=True, help="exported I/O list CSV")
    parser.add_argument("--device-ip", required=True, help="simulator address seen from OpenPLC")
    parser.add_argument("--device-port", type=int, default=5020)
    parser.add_argument("--device-name", default="OpenFactory")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--user", default="openplc")
    parser.add_argument("--password", default="openplc")
    parser.add_argument("--no-start", action="store_true", help="configure but do not start")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    ranges = table_ranges(args.csv)
    print(
        "Slave Device ranges from the CSV: "
        + ", ".join(f"{t} start {s} size {n}" for t, (s, n) in ranges.items())
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            login(page, args.url, args.user, args.password)
            stop_plc(page, args.url)
            log = upload_program(page, args.url, args.program)
            print(f"uploaded {args.program.name}: {log.strip().splitlines()[-1]}")
            if delete_device(page, args.url, args.device_name):
                print(f"deleted the existing {args.device_name} device")
            add_device(page, args.url, args.device_name, args.device_ip, args.device_port, ranges)
            print(f"Slave Devices as OpenPLC shows them:\n{device_table(page, args.url)}")
            if not args.no_start:
                print(f"PLC: {start_plc(page, args.url)}")
        except (RuntimeError, TimeoutError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
