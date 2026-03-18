#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
if [[ -z "$MODE" ]]; then
  echo "Usage: ./deploy/ssl.sh <init|renew|activate|uninstall-cron> [--domain <domain>] [--email <email>] [extra renew args]"
  exit 1
fi
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
LE_LIVE_DIR="$PROJECT_DIR/deploy/letsencrypt/live"
ACME_WEBROOT="/var/www/certbot"
CRON_MARKER="# live-voice-ssl-renew"

DOMAIN_OVERRIDE=""
EMAIL_OVERRIDE=""
EXTRA_ARGS=()

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
    domain="$(extract_domain_from_url "${PUBLIC_INTERVIEW_BASE_URL:-}")"
  fi
  if [[ -z "$domain" ]]; then
    echo "[ssl] Missing domain. Set PUBLIC_INTERVIEW_BASE_URL in .env or pass --domain" >&2
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
  compose exec -T gateway nginx -t
  compose exec -T gateway nginx -s reload
}

wait_backend_startup_or_fail() {
  local timeout_seconds="${INIT_BACKEND_STARTUP_TIMEOUT_SECONDS:-60}"
  local waited=0

  echo "[ssl] Waiting backend startup self-check (timeout=${timeout_seconds}s)"
  while (( waited < timeout_seconds )); do
    local logs
    logs="$(compose logs --no-color --tail 400 backend 2>/dev/null || true)"
    local latest_startup_logs
    latest_startup_logs="$(
      echo "$logs" | awk '
        /\[Server\] startup begin/ {block=$0 ORS; seen=1; next}
        {if (seen) block=block $0 ORS}
        END {if (seen) printf "%s", block; else printf "%s", $0}
      '
    )"
    if [[ -z "$latest_startup_logs" ]]; then
      latest_startup_logs="$logs"
    fi

    if echo "$latest_startup_logs" | grep -Fq "[StartupSelfCheck] failed, aborting server startup"; then
      echo "[ssl] Backend startup self-check failed; stopping services and aborting init"
      compose stop gateway backend >/dev/null || true
      echo "$latest_startup_logs" | tail -n 60
      return 1
    fi

    if echo "$latest_startup_logs" | grep -Fq "[StartupSelfCheck] summary status=PASS"; then
      if echo "$latest_startup_logs" | grep -Fq "WebSocket server is running on"; then
        if echo "$latest_startup_logs" | grep -Fq "HTTP log server is running on"; then
          echo "[ssl] Backend startup passed"
          return 0
        fi
        if echo "$latest_startup_logs" | grep -Fq "Admin API server is running on"; then
          echo "[ssl] Backend startup passed"
          return 0
        fi
      fi
    fi

    sleep 1
    waited=$((waited + 1))
  done

  echo "[ssl] Backend startup check timed out; stopping services and aborting init"
  compose logs --no-color --tail 80 backend 2>/dev/null || true
  compose stop gateway backend >/dev/null || true
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

  echo "[ssl] Starting gateway/backend"
  compose up -d --build gateway backend
  wait_backend_startup_or_fail

  local existing_cert
  existing_cert="$(pick_latest_cert_dir "$domain" || true)"
  if [[ -n "$existing_cert" ]]; then
    echo "[ssl] Reusing existing certificate for $domain: $existing_cert"
  else
    echo "[ssl] No existing certificate found, requesting certificate for $domain"
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
    echo "Usage: ./deploy/ssl.sh <init|renew|activate|uninstall-cron> [--domain <domain>] [--email <email>] [extra renew args]"
    exit 1
    ;;
esac
