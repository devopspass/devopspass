#!/usr/bin/env bash

set -euo pipefail

FASTAPI_HOST="${FASTAPI_HOST:-0.0.0.0}"
FASTAPI_PORT="${FASTAPI_PORT:-10818}"
FASTAPI_RELOAD="${FASTAPI_RELOAD:-true}"
WATCHFILES_FORCE_POLLING="${WATCHFILES_FORCE_POLLING:-true}"
WATCHFILES_POLL_DELAY_MS="${WATCHFILES_POLL_DELAY_MS:-500}"
PROJECT_ROOT="/workspace"
SSH_AUTH_SOCK_DEFAULT="/tmp/dop-ssh-agent.sock"
SSH_AGENT_PID_FILE="/tmp/dop-ssh-agent.pid"

mkdir -p "${DOP_DATA_DIR:-/workspace/.data}"

start_ssh_agent_if_needed() {
  local sock_path="${SSH_AUTH_SOCK:-$SSH_AUTH_SOCK_DEFAULT}"

  if [[ -S "$sock_path" ]]; then
    export SSH_AUTH_SOCK="$sock_path"
    if ssh-add -l >/dev/null 2>&1 || [[ $? -eq 1 ]]; then
      return
    fi
  fi

  rm -f "$sock_path"
  local agent_output
  agent_output="$(ssh-agent -s -a "$sock_path")"
  eval "$agent_output" >/dev/null
  export SSH_AUTH_SOCK="$sock_path"
  echo "${SSH_AGENT_PID:-}" > "$SSH_AGENT_PID_FILE"
}

start_ssh_agent_if_needed

if [[ "${1:-api}" == "api" ]]; then
  cd "${PROJECT_ROOT}/api"

  uvicorn_args=(
    main:app
    --host "${FASTAPI_HOST}"
    --port "${FASTAPI_PORT}"
  )

  if [[ "${FASTAPI_RELOAD,,}" == "true" ]]; then
    if [[ "${WATCHFILES_FORCE_POLLING,,}" == "true" ]]; then
      export WATCHFILES_FORCE_POLLING=true
      export WATCHFILES_POLL_DELAY_MS="${WATCHFILES_POLL_DELAY_MS}"
    fi

    uvicorn_args+=(
      --reload
      --reload-dir "${PROJECT_ROOT}/api"
      --reload-dir "${PROJECT_ROOT}/plugins"
    )
  fi

  exec uvicorn "${uvicorn_args[@]}"
fi

exec "$@"
