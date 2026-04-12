#!/bin/sh
set -eu

DASH_DIR="/var/lib/grafana/dashboards"
mkdir -p "$DASH_DIR"

fetch_dashboard() {
  url="$1"
  out="$2"
  tmp="${out}.tmp"
  if [ -s "$out" ]; then
    return 0
  fi
  if command -v curl >/dev/null 2>&1 && curl -fsSL "$url" -o "$tmp"; then
    mv "$tmp" "$out"
    echo "downloaded dashboard: $out"
  elif command -v wget >/dev/null 2>&1 && wget -qO "$tmp" "$url"; then
    mv "$tmp" "$out"
    echo "downloaded dashboard: $out"
  else
    rm -f "$tmp"
    echo "warn: failed to download dashboard from $url (curl/wget unavailable or network error)"
  fi
}

fetch_dashboard "https://grafana.com/api/dashboards/1860/revisions/37/download" "$DASH_DIR/node-exporter-full.json"
fetch_dashboard "https://grafana.com/api/dashboards/11835/revisions/1/download" "$DASH_DIR/redis-overview.json"

echo "dashboard bootstrap done"
