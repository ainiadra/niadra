#!/bin/sh
# Writes the Console runtime config (/config.json, next to index.html) from the environment.
#
#   NIADRA_CONTROL_URL=https://control.1-2-3-4.sslip.io \
#   NIADRA_API_URL=https://api.1-2-3-4.sslip.io \
#   sh deploy/console-config.sh dist/config.json
#
# NIADRA_CELLS is optional: "cell_id_or_region=url,..." for spaces served by another data API.
# Every URL must be https (http only for localhost).
set -eu

out="${1:-/dev/stdout}"
: "${NIADRA_CONTROL_URL:?set NIADRA_CONTROL_URL}"
api="${NIADRA_API_URL:-}"
cells_in="${NIADRA_CELLS:-}"
if [ -z "$api" ] && [ -z "$cells_in" ]; then
  echo "console-config.sh: set NIADRA_API_URL or NIADRA_CELLS" >&2
  exit 1
fi

check_url() {
  case "$1" in
    https://*) ;;
    http://localhost* | http://127.0.0.1*) ;;
    *) echo "console-config.sh: $1 is not https" >&2; exit 1 ;;
  esac
  case "$1" in
    *'"'* | *'\'* | *' '*) echo "console-config.sh: $1 has a character that is not allowed" >&2; exit 1 ;;
  esac
}

check_url "$NIADRA_CONTROL_URL"
[ -n "$api" ] && check_url "$api"
cells=""
if [ -n "$cells_in" ]; then
  old_ifs=$IFS
  IFS=','
  for pair in $cells_in; do
    key=${pair%%=*}
    url=${pair#*=}
    if [ -z "$key" ] || [ "$key" = "$pair" ]; then
      echo "console-config.sh: '$pair' is not key=url" >&2; exit 1
    fi
    case "$key" in
      *[!a-z0-9-]*) echo "console-config.sh: '$key' must be lowercase letters, digits and dashes" >&2; exit 1 ;;
    esac
    check_url "$url"
    cells="${cells:+$cells, }\"$key\": \"$url\""
  done
  IFS=$old_ifs
fi

tmp="$(mktemp)"
{
  printf '{\n  "controlURL": "%s"' "$NIADRA_CONTROL_URL"
  [ -n "$api" ] && printf ',\n  "apiURL": "%s"' "$api"
  [ -n "$cells" ] && printf ',\n  "cells": { %s }' "$cells"
  printf '\n}\n'
} > "$tmp"
if [ "$out" = /dev/stdout ]; then cat "$tmp"; rm -f "$tmp"; else chmod 0644 "$tmp"; mv "$tmp" "$out"; fi
