#!/usr/bin/env bash

set -euo pipefail

mkdir -p "${DOP_DATA_DIR:-/workspace/.data}"
mkdir -p "${DOP_LOGS_DIR:-/workspace/logs}"

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
