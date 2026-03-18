#!/bin/sh
set -eu

CERT_DIR="/etc/letsencrypt/live/smartinterview.cn"
FULLCHAIN="$CERT_DIR/fullchain.pem"
PRIVKEY="$CERT_DIR/privkey.pem"

if [ -f "$FULLCHAIN" ] && [ -f "$PRIVKEY" ]; then
  echo "[init-cert] Existing certificate found for smartinterview.cn"
  exit 0
fi

echo "[init-cert] No certificate found, generating temporary self-signed certificate"
mkdir -p "$CERT_DIR"

openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 1 \
  -keyout "$PRIVKEY" \
  -out "$FULLCHAIN" \
  -subj "/CN=smartinterview.cn"
