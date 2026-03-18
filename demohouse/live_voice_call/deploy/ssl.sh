#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
if [[ -z "$MODE" ]]; then
  echo "Usage: ./deploy/ssl.sh <init|renew|activate|install-cron> [--domain <domain>] [--email <email>] [extra renew args]"
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

DOMAIN="${DOMAIN_OVERRIDE:-}"
if [[ -z "$DOMAIN" ]]; then
  DOMAIN="$(extract_domain_from_url "${PUBLIC_INTERVIEW_BASE_URL:-}")"
fi
if [[ -z "$DOMAIN" ]]; then
  echo "[ssl] Missing domain. Set PUBLIC_INTERVIEW_BASE_URL in .env or pass --domain"
  exit 1
fi

EMAIL="${EMAIL_OVERRIDE:-${LETSENCRYPT_EMAIL:-}}"
if [[ -z "$EMAIL" ]]; then
  echo "[ssl] Missing email. Set LETSENCRYPT_EMAIL in .env or pass --email"
  exit 1
fi

compose() {
  docker compose "$@"
}

pick_latest_cert_dir() {
  local candidates=()
  local d
  shopt -s nullglob
  for d in "$LE_LIVE_DIR"/"$DOMAIN"*; do
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

activate_latest_cert() {
  local cert_dir
  cert_dir="$(pick_latest_cert_dir)"
  switch_active_link "$cert_dir"

  echo "[ssl] Reloading gateway"
  reload_gateway

  echo "[ssl] Activate completed"
}

run_init() {
  echo "[ssl] Starting gateway/backend"
  compose up -d --build gateway backend

  echo "[ssl] Requesting certificate for $DOMAIN"
  compose --profile certbot run --rm certbot certonly \
    --webroot -w "$ACME_WEBROOT" \
    -d "$DOMAIN" \
    --cert-name "$DOMAIN" \
    -m "$EMAIL" \
    --agree-tos --no-eff-email

  activate_latest_cert

  echo "[ssl] Init completed"
}

run_renew() {
  echo "[ssl] Renewing certificates"
  compose --profile certbot run --rm certbot renew \
    --webroot -w "$ACME_WEBROOT" \
    "${EXTRA_ARGS[@]}"

  activate_latest_cert

  echo "[ssl] Renew completed"
}

install_cron() {
  local cron_cmd
  cron_cmd="0 3 * * * cd $PROJECT_DIR && $SCRIPT_DIR/ssl.sh renew >> /var/log/live-voice-certbot-renew.log 2>&1 $CRON_MARKER"

  local current
  current="$(crontab -l 2>/dev/null || true)"

  {
    echo "$current" | sed '/live-voice-ssl-renew/d'
    echo "$cron_cmd"
  } | crontab -

  echo "[ssl] Cron installed"
  crontab -l | grep 'live-voice-ssl-renew'
}

case "$MODE" in
  init)
    run_init
    ;;
  renew)
    run_renew
    ;;
  activate)
    activate_latest_cert
    ;;
  install-cron)
    install_cron
    ;;
  *)
    echo "Unknown mode: $MODE"
    echo "Usage: ./deploy/ssl.sh <init|renew|activate|install-cron> [--domain <domain>] [--email <email>] [extra renew args]"
    exit 1
    ;;
esac
