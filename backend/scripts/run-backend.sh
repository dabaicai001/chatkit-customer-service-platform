#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backend"
VENV_DIR="${BACKEND_DIR}/.venv"
PORT="${PORT:-8001}"

# 自动加载环境变量(密钥来源):优先 ENV_FILE 指定的文件,默认 backend/.env
# 平台注入的环境变量(Docker -e / K8s Secret / systemd)优先级更高,不受影响
ENV_FILE="${ENV_FILE:-${BACKEND_DIR}/.env}"
if [ -f "${ENV_FILE}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
  echo "[run-backend] 已加载环境变量文件: ${ENV_FILE}"
else
  echo "[run-backend] 未发现 ${ENV_FILE},使用平台注入的环境变量"
fi

UV_PROJECT_ENVIRONMENT="${VENV_DIR}" uv sync --directory "${BACKEND_DIR}"
exec "${VENV_DIR}/bin/python" -m uvicorn app.main:app --app-dir "${BACKEND_DIR}" --reload --port "${PORT}"
