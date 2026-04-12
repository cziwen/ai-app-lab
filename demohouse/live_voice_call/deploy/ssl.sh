#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
if [[ -z "$MODE" ]]; then
  echo "Usage: ./deploy/ssl.sh <init|renew|activate|uninstall-cron> [--domain <domain>] [--email <email>] [--stop-nonessential|--no-stop-nonessential] [--stop-extra-processes|--no-stop-extra-processes] [extra renew args]"
  exit 1
fi
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
LE_LIVE_DIR="$PROJECT_DIR/deploy/letsencrypt/live"
ACME_WEBROOT="/var/www/certbot"
CRON_MARKER="# live-voice-ssl-renew"
SWAPFILE_PATH="${SWAPFILE_PATH:-/swapfile}"
SWAPFILE_SIZE_GB="${SWAPFILE_SIZE_GB:-2}"
FRONTEND_NODE_OPTIONS="${FRONTEND_NODE_OPTIONS:---max-old-space-size=512}"
STOP_NONESSENTIAL_CONTAINERS_DEFAULT="${STOP_NONESSENTIAL_CONTAINERS_DEFAULT:-1}"
STOP_EXTRA_PROCESSES_DEFAULT="${STOP_EXTRA_PROCESSES_DEFAULT:-0}"
EXTRA_STOP_PATTERNS="${EXTRA_STOP_PATTERNS:-code-server|vscode-server|AliYunDunMonito|aegis_cli}"
GRAFANA_WAIT_TIMEOUT_SECONDS="${GRAFANA_WAIT_TIMEOUT_SECONDS:-180}"
KEEP_SERVICES=(backend gateway certbot grafana prometheus loki promtail node-exporter redis-exporter redis)

DOMAIN_OVERRIDE=""
EMAIL_OVERRIDE=""
EXTRA_ARGS=()
STOP_NONESSENTIAL_CONTAINERS="$STOP_NONESSENTIAL_CONTAINERS_DEFAULT"
STOP_EXTRA_PROCESSES="$STOP_EXTRA_PROCESSES_DEFAULT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN_OVERRIDE="${2:-}"
      shift 2
      ;;
    --email)
      EMAIL_OVERRIDE="${2:-}"
      shift 2
      ;;
    --stop-nonessential)
      STOP_NONESSENTIAL_CONTAINERS="1"
      shift
      ;;
    --no-stop-nonessential)
      STOP_NONESSENTIAL_CONTAINERS="0"
      shift
      ;;
    --stop-extra-processes)
      STOP_EXTRA_PROCESSES="1"
      shift
      ;;
    --no-stop-extra-processes)
      STOP_EXTRA_PROCESSES="0"
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

extract_domain_from_url() {
  local input="$1"
  input="${input#http://}"
  input="${input#https://}"
  input="${input%%/*}"
  input="${input%%:*}"
  echo "$input"
}

resolve_domain() {
  local domain="${DOMAIN_OVERRIDE:-}"
  if [[ -z "$domain" ]]; then
    domain="$(extract_domain_from_url "${INTERVIEW_BASE_DOMAIN:-}")"
  fi
  if [[ -z "$domain" ]]; then
    echo "[ssl] Missing domain. Set INTERVIEW_BASE_DOMAIN in .env or pass --domain" >&2
    return 1
  fi

  echo "$domain"
}

resolve_email() {
  local email="${EMAIL_OVERRIDE:-${LETSENCRYPT_EMAIL:-}}"
  if [[ -z "$email" ]]; then
    echo "[ssl] Missing email. Set LETSENCRYPT_EMAIL in .env or pass --email" >&2
    return 1
  fi

  echo "$email"
}

compose() {
  docker compose "$@"
}

print_precheck() {
  echo "[ssl] ===== Precheck ====="
  free -h || true
  echo "[ssl] ----- swap -----"
  swapon --show || true
  echo "[ssl] ----- running containers -----"
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' || true
  echo "[ssl] ===================="
}

compose_build_serial() {
  if compose build --help 2>/dev/null | grep -q -- "--no-parallel"; then
    compose build --no-parallel "$@"
  else
    echo "[ssl] docker compose build does not support --no-parallel, using COMPOSE_PARALLEL_LIMIT=1"
    COMPOSE_PARALLEL_LIMIT=1 compose build "$@"
  fi
}

stop_project_containers() {
  echo "[ssl] Stopping current project containers (gateway/backend/certbot) to free memory"
  compose stop gateway backend certbot || true
}

stop_nonessential_containers() {
  if [[ "$STOP_NONESSENTIAL_CONTAINERS" != "1" ]]; then
    echo "[ssl] Skip stopping nonessential containers (STOP_NONESSENTIAL_CONTAINERS=0)"
    return 0
  fi

  local names=()
  local ids=()
  local keep_id
  local id
  local name
  local service
  declare -A keep_ids=()

  for service in "${KEEP_SERVICES[@]}"; do
    while IFS= read -r keep_id; do
      [[ -z "$keep_id" ]] && continue
      keep_ids["$keep_id"]=1
    done < <(compose ps -q "$service" 2>/dev/null || true)
  done

  while IFS='|' read -r id name; do
    [[ -z "$id" ]] && continue
    if [[ -n "${keep_ids[$id]:-}" ]]; then
      continue
    fi
    names+=("$name")
    ids+=("$id")
  done < <(docker ps --format '{{.ID}}|{{.Names}}')

  if [[ ${#ids[@]} -eq 0 ]]; then
    echo "[ssl] No nonessential running containers"
    return 0
  fi

  echo "[ssl] Stopping nonessential containers: ${names[*]}"
  docker stop "${ids[@]}" || true
}

restart_project_stack_atomic() {
  echo "[ssl] Restarting compose project atomically (down -> up), preserving named volumes"
  compose down --remove-orphans || true
}

stop_extra_processes() {
  if [[ "$STOP_EXTRA_PROCESSES" != "1" ]]; then
    echo "[ssl] Skip stopping extra host processes (STOP_EXTRA_PROCESSES=0)"
    return 0
  fi

  if [[ "$(id -u)" -ne 0 ]]; then
    echo "[ssl] Cannot stop extra processes without root, skipping"
    return 0
  fi

  echo "[ssl] Stopping extra host processes by pattern: $EXTRA_STOP_PATTERNS"
  pkill -f "$EXTRA_STOP_PATTERNS" || true
}

ensure_swap() {
  local current_swap
  current_swap="$(swapon --show --bytes --noheadings 2>/dev/null || true)"
  if [[ -n "$current_swap" ]]; then
    echo "[ssl] Swap already enabled, skip creating swapfile"
    return 0
  fi

  if [[ "$(id -u)" -ne 0 ]]; then
    echo "[ssl] Swap is disabled and current user is not root. Please enable swap manually." >&2
    return 1
  fi

  local target_bytes
  target_bytes=$((SWAPFILE_SIZE_GB * 1024 * 1024 * 1024))

  if [[ ! -f "$SWAPFILE_PATH" ]]; then
    echo "[ssl] Creating ${SWAPFILE_SIZE_GB}G swapfile at $SWAPFILE_PATH"
    if command -v fallocate >/dev/null 2>&1; then
      fallocate -l "$target_bytes" "$SWAPFILE_PATH"
    else
      dd if=/dev/zero of="$SWAPFILE_PATH" bs=1M count=$((SWAPFILE_SIZE_GB * 1024))
    fi
    chmod 600 "$SWAPFILE_PATH"
    mkswap "$SWAPFILE_PATH"
  fi

  echo "[ssl] Enabling swapfile $SWAPFILE_PATH"
  swapon "$SWAPFILE_PATH"

  if ! grep -qE "^[^#]*[[:space:]]$SWAPFILE_PATH[[:space:]]" /etc/fstab 2>/dev/null; then
    echo "$SWAPFILE_PATH none swap sw 0 0" >> /etc/fstab
    echo "[ssl] Added swapfile to /etc/fstab"
  fi
}

pick_latest_cert_dir() {
  local domain="$1"
  local candidates=()
  local d
  shopt -s nullglob
  for d in "$LE_LIVE_DIR"/"$domain"*; do
    [[ -d "$d" ]] || continue
    [[ -f "$d/fullchain.pem" && -f "$d/privkey.pem" ]] || continue
    candidates+=("$d")
  done
  shopt -u nullglob

  if [[ ${#candidates[@]} -eq 0 ]]; then
    return 1
  fi

  ls -td "${candidates[@]}" | head -n1
}

switch_active_link() {
  local cert_dir="$1"
  local cert_name
  cert_name="$(basename "$cert_dir")"

  mkdir -p "$LE_LIVE_DIR"
  (
    cd "$LE_LIVE_DIR"
    ln -sfn "$cert_name" "__active__"
  )

  echo "[ssl] selected cert dir: $cert_dir"
  echo "[ssl] __active__ -> $cert_name"
}

reload_gateway() {
  wait_for_service_running grafana "$GRAFANA_WAIT_TIMEOUT_SECONDS"
  wait_for_service_running gateway 30
  compose exec -T gateway nginx -t
  compose exec -T gateway nginx -s reload
}

wait_for_service_running() {
  local service="$1"
  local timeout="${2:-60}"
  local waited=0
  local interval=2

  echo "[ssl] Waiting for service '$service' to be running (timeout: ${timeout}s)"
  while (( waited < timeout )); do
    local running
    running="$(compose ps --status running --services "$service" 2>/dev/null || true)"
    if [[ "$running" == "$service" ]]; then
      echo "[ssl] Service '$service' is running"
      return 0
    fi
    sleep "$interval"
    waited=$((waited + interval))
  done

  echo "[ssl] Service '$service' failed to reach running state within ${timeout}s" >&2
  echo "[ssl] Debug hint: docker compose ps $service" >&2
  echo "[ssl] Debug hint: docker compose logs --tail=100 $service" >&2
  return 1
}

activate_latest_cert() {
  local domain="$1"
  local cert_dir
  cert_dir="$(pick_latest_cert_dir "$domain")"
  switch_active_link "$cert_dir"

  echo "[ssl] Reloading gateway"
  reload_gateway

  echo "[ssl] Activate completed"
}

run_init() {
  local domain
  local email
  domain="$(resolve_domain)"
  email="$(resolve_email)"

  print_precheck
  stop_nonessential_containers
  stop_extra_processes
  restart_project_stack_atomic

  echo "[ssl] Ensuring swap for low-memory deployment"
  ensure_swap

  echo "[ssl] Building backend (serial)"
  compose_build_serial backend
  echo "[ssl] Starting backend"
  compose up -d backend

  echo "[ssl] Building gateway (serial, FRONTEND_NODE_OPTIONS=$FRONTEND_NODE_OPTIONS)"
  compose_build_serial --build-arg FRONTEND_NODE_OPTIONS="$FRONTEND_NODE_OPTIONS" gateway

  local existing_cert
  existing_cert="$(pick_latest_cert_dir "$domain" || true)"
  if [[ -n "$existing_cert" ]]; then
    echo "[ssl] Reusing existing certificate for $domain: $existing_cert"
  else
    echo "[ssl] No existing certificate found, will request certificate after gateway is up"
  fi

  echo "[ssl] Starting observability stack"
  compose up -d prometheus loki promtail node-exporter redis-exporter grafana
  wait_for_service_running grafana "$GRAFANA_WAIT_TIMEOUT_SECONDS"

  echo "[ssl] Starting gateway"
  compose up -d gateway
  wait_for_service_running gateway 30

  if [[ -z "$existing_cert" ]]; then
    echo "[ssl] Requesting certificate for $domain"
    compose --profile certbot run --rm certbot certonly \
      --webroot -w "$ACME_WEBROOT" \
      -d "$domain" \
      --cert-name "$domain" \
      -m "$email" \
      --agree-tos --no-eff-email
  fi

  activate_latest_cert "$domain"

  echo "[ssl] Init completed"
}

run_renew() {
  local domain
  domain="$(resolve_domain)"

  echo "[ssl] Renewing certificates"
  compose --profile certbot run --rm certbot renew \
    --webroot -w "$ACME_WEBROOT" \
    "${EXTRA_ARGS[@]}"

  activate_latest_cert "$domain"

  echo "[ssl] Renew completed"
}

uninstall_cron() {
  local current
  current="$(crontab -l 2>/dev/null || true)"

  if [[ -z "$current" ]]; then
    echo "[ssl] No crontab found, nothing to remove"
    return 0
  fi

  local updated
  updated="$(echo "$current" | sed '/live-voice-ssl-renew/d')"
  if [[ "$updated" == "$current" ]]; then
    echo "[ssl] No auto-renew cron entry found ($CRON_MARKER)"
    return 0
  fi

  echo "$updated" | crontab -
  echo "[ssl] Removed auto-renew cron entry ($CRON_MARKER)"
}

case "$MODE" in
  init)
    run_init
    ;;
  renew)
    run_renew
    ;;
  activate)
    activate_latest_cert "$(resolve_domain)"
    ;;
  uninstall-cron)
    uninstall_cron
    ;;
  *)
    echo "Unknown mode: $MODE"
    echo "Usage: ./deploy/ssl.sh <init|renew|activate|uninstall-cron> [--domain <domain>] [--email <email>] [--stop-nonessential|--no-stop-nonessential] [--stop-extra-processes|--no-stop-extra-processes] [extra renew args]"
    exit 1
    ;;
esac
