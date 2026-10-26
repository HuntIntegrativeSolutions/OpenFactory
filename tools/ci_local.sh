#!/usr/bin/env bash
# OpenFactory CI steps (PLAN 1.15). GitHub Actions (.github/workflows/ci.yml) runs this same script
# after installing Python, Node, and Playwright's Chromium, so CI and a local run cannot drift.
#
# Run locally in a fresh shell, from the repository root:
#   env -i HOME="$HOME" PATH="$PATH" bash tools/ci_local.sh
#
# Steps, in order; the first failure stops the script with a non-zero exit:
#   1. uv sync --frozen --all-extras (exact versions from uv.lock, logix and opcua extras included,
#      so a local run never strips them from the dev venv)
#   2. ruff check and ruff format --check
#   3. scene JSON Schema is current; every shipped scene validates
#   4. npm ci, npm run build (web/dist must exist afterwards), npm test
#   5. Playwright's Chromium is present (installed by the workflow; never installed here)
#   6. pytest with CI=true: the screenshot and parity tests fail instead of skipping when the viewer
#      build, node, or Chromium is missing
#   7. tools/screenshot.py on the shipped scene, with its non-blank PNG check
#   8. tools/parity_trace.py --check (committed parity traces match the kinematic model)
#   9. the working tree is unchanged (no step rewrote a lockfile, trace, or schema)
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
step() {
  STEP=$((STEP + 1))
  printf '\n=== [%d] %s\n' "$STEP" "$1"
}
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

step "pytest (CI=true: no skipped viewer, screenshot, or parity tests)"
uv run pytest -q -rs

step "screenshot runner with non-blank check"
uv run python tools/screenshot.py scenes/sorting-by-height.yaml --seconds 3 \
  --out tools/screenshots/ci-sorting-by-height.png \
  --force CONV_01_RUN_DO=1 --force CONV_02_RUN_DO=1 --force EMT_01_EN_DO=1

step "parity traces current"
uv run python tools/parity_trace.py --check
echo "web/src/fixtures/parity-*.json match core/kinematics.py"

step "working tree unchanged"
git status --short
git diff --exit-code

printf '\nCI OK: %d steps in %d s\n' "$STEP" "$((SECONDS - started))"
