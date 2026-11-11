#!/usr/bin/env bash
# OpenFactory CI steps (PLAN 1.15). GitHub Actions (.github/workflows/ci.yml) runs this same script
# after installing Python, Node, Playwright's Chromium and Mosquitto (the Sparkplug tests' broker,
# PLAN 3.5), so CI and a local run cannot drift. Locally, install Mosquitto first
# (`sudo apt-get install mosquitto`): under CI=true its tests fail instead of skipping.
#
# Run locally in a fresh shell, from the repository root:
#   env -i HOME="$HOME" PATH="$PATH" bash tools/ci_local.sh
#
# Steps, in order; the first failure stops the script with a non-zero exit:
#   1. uv sync --frozen --all-extras (exact versions from uv.lock, logix and opcua extras included,
#      so a local run never strips them from the dev venv)
#   2. ruff check and ruff format --check
#   3. scene JSON Schema is current; every shipped scene validates and lints with no errors, and
#      is at the current format version (openfactory migrate --check, PLAN 4.7)
#   4. every shipped scene passes its checks under its stand-in script (PLAN 2.11)
#   5. npm ci, npm run build (web/dist must exist afterwards), npm test
#   6. Playwright's Chromium is present (installed by the workflow; never installed here)
#   7. tools/screenshot.py on the shipped scene under its stand-in script, with its non-blank PNG
#      check, its tilt check, its position check and its default-models check (before pytest, so test_screenshot_check has
#      a real Playwright PNG to decode)
#   8. pytest with CI=true and -rfEs: the screenshot and parity tests fail instead of skipping when
#      the viewer build, node, Chromium, or a screenshot is missing; the run must show zero skips
#   9. tools/parity_trace.py --check (committed parity traces match the kinematic model)
#  10. tools/build_kit.py --check (every demo kit is current, PLAN 2.14)
#  11. tools/bench.py: the tick cost within 25 % of tools/bench_baseline.json (PLAN 2.9)
#  12. tools/sbom.py --check: the CycloneDX SBOM builds, and every component is in THIRD_PARTY.md
#      with an allowed license (PLAN 4.8; written to build/sbom/, never committed)
#  13. mkdocs build --strict: the docs site builds with no warnings (PLAN 4.4; into build/site,
#      which .github/workflows/docs.yml deploys to GitHub Pages from main)
#  14. the working tree is unchanged (no step rewrote a lockfile, trace, or schema)
#
# Training and demo tool; not a safety-rated simulation.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
export CI=true
# Drives without symlink support (WSL /mnt/*) need the venv elsewhere and copied installs
# (CONTRIBUTING.md, "Required setup on drives without symlink support").
if [[ "$REPO" == /mnt/* ]]; then
  export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/openfactory}"
  export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
fi

STEP=0
STEP_NAME=""
step() {
  STEP=$((STEP + 1))
  STEP_NAME="$1"
  printf '\n=== [%d] %s\n' "$STEP" "$1"
}
# On GitHub Actions a failure is also a workflow annotation naming the step, and pytest's FAILED
# lines become annotations too: annotations are readable through the public check-runs API, the
# job log only when signed in (PLAN 2.9; a failed run on main could not be diagnosed otherwise).
annotate_failure() {
  [[ "${GITHUB_ACTIONS:-}" == "true" ]] || return 0
  echo "::error title=tools/ci_local.sh step ${STEP}::step ${STEP} (${STEP_NAME}) failed"
  if [[ "$STEP_NAME" == pytest* && -f tools/screenshots/ci-pytest.log ]]; then
    # each failed, errored or skipped test, then pytest's closing count line
    grep -E "^(FAILED|ERROR|SKIPPED) " tools/screenshots/ci-pytest.log | head -20 | while IFS= read -r line; do
      echo "::error title=pytest::${line//$'\n'/ }"
    done
    grep -E "[0-9]+ (passed|failed|errors?|skipped).* in [0-9.]+s" tools/screenshots/ci-pytest.log | tail -1 |
      while IFS= read -r line; do echo "::error title=pytest count::${line}"; done
  fi
}
trap annotate_failure ERR
started=$SECONDS

step "uv sync --frozen --all-extras"
uv --version
uv sync --frozen --all-extras

step "ruff"
uv run ruff check .
uv run ruff format --check .

step "scene schema and shipped scenes"
uv run python -m openfactory.core.schema --check
echo "web/public/scene.schema.json is current"
uv run openfactory validate scenes
uv run openfactory lint scenes
uv run openfactory migrate --check scenes scenes/examples scenes/_library  # PLAN 4.7

step "exercise checks: every shipped scene under its stand-in script (PLAN 2.11)"
for scene in scenes/*.yaml; do
  case "$scene" in *.modbus.yaml) continue ;; esac
  name="$(basename "$scene" .yaml)"
  echo "--- $name"
  uv run openfactory test "$scene" --script "examples/pyplc/$name.py" -q
done

step "web: npm ci, npm run build, npm test"
node --version
(
  cd web
  npm ci --no-audit --no-fund
  npm run build
  test -f dist/index.html || { echo "web/dist/index.html missing after npm run build" >&2; exit 1; }
  echo "web/dist/index.html built"
  npm test
)

step "Playwright Chromium present"
uv run python - <<'PY'
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    path = Path(pw.chromium.executable_path)
if not path.is_file():
    raise SystemExit(f"Chromium missing at {path}; run: uv run playwright install chromium")
print(f"chromium: {path}")
PY

step "screenshot runner with non-blank check"
# 20 s under the stand-in script: several tall boxes pushed onto CONV_02 (PLAN 2.9, the divert),
# drawn with the default models of the types listed, merged under 45 draw calls (PLAN 4.2)
uv run python tools/screenshot.py scenes/sorting-by-height.yaml --seconds 20 \
  --out tools/screenshots/ci-sorting-by-height.png \
  --script examples/pyplc/sorting-by-height.py --max-tilt-deg 5 --max-off-deck-m 0.05 \
  --housings belt_conveyor,photo_eye,pusher --max-draw-calls 44

step "pytest (CI=true: zero skips allowed)"
# -rfEs, not -rs: -r replaces pytest's default "fE", and the FAILED and ERROR summary lines are what
# a failure annotation quotes (run 42365962279 failed here with none to quote)
uv run pytest -q -rfEs | tee tools/screenshots/ci-pytest.log
if grep -Eq "[0-9]+ skipped" tools/screenshots/ci-pytest.log; then
  echo "pytest skipped tests under CI=true; every prerequisite must be present" >&2
  annotate_failure # an explicit exit does not fire the ERR trap
  exit 1
fi

step "parity traces current"
uv run python tools/parity_trace.py --check
echo "web/src/fixtures/parity-*.json match core/kinematics.py"

step "demo kits current (PLAN 2.14)"
uv run python tools/build_kit.py --check

step "tick-cost gate (PLAN 2.9)"
uv run python tools/bench.py

step "SBOM (PLAN 4.8)"
uv run python tools/sbom.py --check

step "docs site (PLAN 4.4)"
uv run mkdocs build --strict

step "working tree unchanged"
git status --short
git diff --exit-code

printf '\nCI OK: %d steps in %d s\n' "$STEP" "$((SECONDS - started))"
