#!/usr/bin/env bash

set -u

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONTAINER_RUNTIME=""
COMPOSE_CMD=""

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_section() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }


detect_runtime() {
  if command -v docker >/dev/null 2>&1 && command -v docker-compose >/dev/null 2>&1; then
    CONTAINER_RUNTIME="docker"
    COMPOSE_CMD="docker-compose"
    return 0
  elif command -v podman >/dev/null 2>&1 && command -v podman-compose >/dev/null 2>&1; then
    CONTAINER_RUNTIME="podman"
    COMPOSE_CMD="podman-compose"
    if [ -S "/var/run/docker.sock" ]; then
      export DOCKER_HOST="unix:///var/run/docker.sock"
      export CONTAINER_HOST="unix:///var/run/docker.sock"
      export PODMAN_HOST="unix:///var/run/docker.sock"
    fi
    return 0
  fi
  return 1
}

check_dependencies() {
  log_section "Checking Dependencies"
  if ! detect_runtime; then
    log_error "Neither Docker nor Podman (with compose) is available"
    return 1
  fi

  if [ "$CONTAINER_RUNTIME" = "docker" ]; then
    log_info "Using Docker: $(docker --version)"
    log_info "Docker Compose: $(docker-compose --version)"
  else
    log_info "Using Podman: $(podman --version)"
    log_info "Podman Compose: $(podman-compose --version)"
  fi
}

build_image() {
  log_section "Building Images"
  cd "$SCRIPT_DIR"
  detect_runtime || return 1
  $COMPOSE_CMD build
}

start_stack() {
  log_section "Starting Dev Stack"
  cd "$SCRIPT_DIR"
  detect_runtime || return 1
  $COMPOSE_CMD up -d
  sleep 2
  status
}

stop_stack() {
  log_section "Stopping Dev Stack"
  cd "$SCRIPT_DIR"
  detect_runtime || return 1
  $COMPOSE_CMD down
}

status() {
  log_section "Compose Status"
  cd "$SCRIPT_DIR"
  detect_runtime || return 1
  $COMPOSE_CMD ps
}

logs() {
  log_section "Logs"
  cd "$SCRIPT_DIR"
  detect_runtime || return 1
  local service="${1:-api}"
  $COMPOSE_CMD logs -f "$service"
}

test_api() {
  log_section "Testing API"
  if curl -sSf "http://localhost:10818/status" >/dev/null; then
    log_info "API is healthy"
    curl -s "http://localhost:10818/status"
  else
    log_error "API is not healthy"
    return 1
  fi
}

shell() {
  cd "$SCRIPT_DIR"
  detect_runtime || return 1
  local service="${1:-api}"
  $COMPOSE_CMD exec "$service" sh
}

execute() {
  cd "$SCRIPT_DIR"
  detect_runtime || return 1
  local service="$1"
  shift
  $COMPOSE_CMD exec "$service" sh -lc "$*"
}

show_help() {
  cat << EOF

${BLUE}Docker/Podman Helper for DevOps Pass AI${NC}

${GREEN}Usage: ./docker-helper.sh [command]${NC}

${YELLOW}Commands:${NC}
  check                Check runtime dependencies
  build                Build compose images
  start                Start API + UI stack
  stop                 Stop stack
  status               Show stack status
  logs [api|ui]        Follow service logs (default: api)
  test-api             Check API health endpoint
  shell [api|ui]       Open shell in service (default: api)
  exec <svc> <cmd>     Execute command in service
  help                 Show help

${YELLOW}Ports:${NC}
  API: http://localhost:10818
  UI : http://localhost:4201

EOF
}

main() {
  local command="${1:-help}"
  case "$command" in
    check)
      check_dependencies
      ;;
    build)
      check_dependencies && build_image
      ;;
    start)
      check_dependencies && start_stack
      ;;
    stop)
      stop_stack
      ;;
    status)
      status
      ;;
    logs)
      logs "${2:-api}"
      ;;
    test-api)
      test_api
      ;;
    shell)
      shell "${2:-api}"
      ;;
    exec)
      if [ $# -lt 3 ]; then
        log_error "Usage: ./docker-helper.sh exec <service> <command>"
        return 1
      fi
      shift
      execute "$@"
      ;;
    help|--help|-h)
      show_help
      ;;
    *)
      log_error "Unknown command: $command"
      show_help
      return 1
      ;;
  esac
}

main "$@"
