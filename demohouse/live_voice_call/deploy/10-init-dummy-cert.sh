#!/bin/sh
set -eu

LIVE_DIR="/etc/letsencrypt/live"
BOOT_DIR="$LIVE_DIR/bootstrap"
ACTIVE_LINK="$LIVE_DIR/__active__"
FULLCHAIN="$BOOT_DIR/fullchain.pem"
PRIVKEY="$BOOT_DIR/privkey.pem"

mkdir -p "$BOOT_DIR"

if [ ! -f "$FULLCHAIN" ] || [ ! -f "$PRIVKEY" ]; then
  echo "[init-cert] No bootstrap certificate found, generating temporary self-signed certificate"
  openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 1 \
    -keyout "$PRIVKEY" \
    -out "$FULLCHAIN" \
    -subj "/CN=bootstrap.invalid"
fi

ln -sfn "$BOOT_DIR" "$ACTIVE_LINK"
echo "[init-cert] Active certificate link prepared: $ACTIVE_LINK -> $BOOT_DIR"
