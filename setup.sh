#!/usr/bin/env bash
# setup.sh — install bsky_weather.py and register a daily cron job.
#
# Tested on Debian 12 (Raspberry Pi OS) and Ubuntu 22.04+.
# References:
#   - python3-venv: https://docs.python.org/3/library/venv.html
#   - cron format:  https://man7.org/linux/man-pages/man5/crontab.5.html

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_NAME="bsky_weather.py"
CRON_HOUR="${CRON_HOUR:-9}"      # local-time hour to run (default 09:00)
CRON_MINUTE="${CRON_MINUTE:-0}"
LOG_FILE="${LOG_FILE:-${PROJECT_DIR}/bsky_weather.log}"

echo "==> Project directory: ${PROJECT_DIR}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "ERROR: ${PYTHON_BIN} not found. Install python3 first." >&2
  exit 1
fi

# python3-venv is a separate package on Debian/Ubuntu.
if ! "${PYTHON_BIN}" -c "import venv" 2>/dev/null; then
  echo "==> Installing python3-venv (sudo required)"
  sudo apt-get update
  sudo apt-get install -y python3-venv
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "==> Creating virtualenv at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

echo "==> Installing dependencies"
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip
pip install -r "${PROJECT_DIR}/requirements.txt"
deactivate

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
  echo "==> Creating .env from .env.example — edit it before the first run"
  cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
  chmod 600 "${PROJECT_DIR}/.env"
fi

# Register a cron entry. Dedupe by checking for the script path.
CRON_CMD="${CRON_MINUTE} ${CRON_HOUR} * * * cd ${PROJECT_DIR} && ${VENV_DIR}/bin/python ${PROJECT_DIR}/${SCRIPT_NAME} >> ${LOG_FILE} 2>&1"
EXISTING="$(crontab -l 2>/dev/null || true)"
if echo "${EXISTING}" | grep -Fq "${PROJECT_DIR}/${SCRIPT_NAME}"; then
  echo "==> Cron entry already exists; leaving as-is"
else
  echo "==> Installing cron entry: ${CRON_CMD}"
  ( echo "${EXISTING}"; echo "${CRON_CMD}" ) | crontab -
fi

echo "==> Done."
echo "    Edit ${PROJECT_DIR}/.env with your Bluesky handle and app password."
echo "    Test once with: DRY_RUN=1 ${VENV_DIR}/bin/python ${PROJECT_DIR}/${SCRIPT_NAME}"
