#!/usr/bin/env bash
# Run once on the Obelisk, by Aman, from the mnemosyne clone (~/helios/mnemosyne). Idempotent: existing
# secrets are kept. Nothing is printed except file names. See docs/deploy.md.
#
# - random service tokens:      secrets/{anki_core_token,propose_token,approve_token}
# - the test sync user:         secrets/anki_sync_test_password, and ANKI_SYNC_TEST_USER in the Helios .env
# - your sync password:         secrets/anki_sync_password, copied from ANKI_SYNC_USER in the Helios .env
# - P3 (TAMU AI Chat, Paperless): secrets/{tamu_api_key,tamu_base_url,paperless_token}, from the Helios .env
# - P4 (metrics):               secrets/influx_token, a bucket-scoped InfluxDB token made in the influxdb container
# - Helios-side tokens:         MNEMOSYNE_APPROVE_TOKEN / MNEMOSYNE_PROPOSE_TOKEN in the Helios .env
set -euo pipefail
cd "$(dirname "$0")/.."
HELIOS_ENV="${HELIOS_ENV:-$HOME/helios/stack/.env}"
TEST_USER="mnemosyne-test"

umask 077
mkdir -p secrets
chmod 700 secrets
rand() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
make() { [ -s "secrets/$1" ] || { rand > "secrets/$1"; echo "created secrets/$1"; }; }

make anki_core_token
make propose_token
make approve_token
make anki_sync_test_password

if [ ! -s secrets/anki_sync_password ]; then
  line="$(grep -E '^ANKI_SYNC_USER=' "$HELIOS_ENV" | head -1 || true)"
  [ -n "$line" ] || { echo "ANKI_SYNC_USER not found in $HELIOS_ENV" >&2; exit 1; }
  printf '%s' "${line#*=}" | cut -d: -f2- | tr -d '\n' > secrets/anki_sync_password
  echo "created secrets/anki_sync_password (from ANKI_SYNC_USER)"
fi
for pair in "tamu_api_key:TAMU_AI_API_KEY" "tamu_base_url:TAMU_BASE_URL" "paperless_token:PAPERLESS_TOKEN"; do   # P3
  file="${pair%%:*}"; key="${pair#*:}"
  if [ ! -s "secrets/$file" ]; then
    v="$(grep -E "^$key=" "$HELIOS_ENV" | head -1 | cut -d= -f2- || true)"
    [ -n "$v" ] && { printf '%s' "$v" > "secrets/$file"; echo "created secrets/$file (from $key)"; }
  fi
done
# P4 metrics: a token that can only read and write the `mnemosyne` bucket, made with the InfluxDB admin token from the
# Helios .env inside the influxdb container. Neither token is printed. Without InfluxDB, an empty file keeps compose happy.
if [ ! -s secrets/influx_token ]; then
  admin="$(grep -E '^INFLUX_ADMIN_TOKEN=' "$HELIOS_ENV" | head -1 | cut -d= -f2- || true)"
  influx="$(docker ps -qf name=helios-influxdb-1 || true)"
  if [ -n "$admin" ] && [ -n "$influx" ]; then
    docker exec -e INFLUX_TOKEN="$admin" "$influx" influx bucket create -n mnemosyne -o helios >/dev/null 2>&1 || true
    bucket="$(docker exec -e INFLUX_TOKEN="$admin" "$influx" influx bucket list -n mnemosyne -o helios --json               | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
    docker exec -e INFLUX_TOKEN="$admin" "$influx" influx auth create -o helios --read-bucket "$bucket"       --write-bucket "$bucket" -d "mnemosyne metrics" --json       | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"], end="")' > secrets/influx_token
    echo "created secrets/influx_token (read/write on the mnemosyne bucket only)"
  else
    : > secrets/influx_token
  fi
fi
# Compose mounts each file into containers running as other uids; the 700 directory keeps other host users out.
chmod 644 secrets/*

add_env() {  # add KEY=VALUE to the Helios .env unless KEY is already there
  grep -qE "^$1=" "$HELIOS_ENV" || { printf '%s=%s\n' "$1" "$2" >> "$HELIOS_ENV"; echo "added $1 to $HELIOS_ENV"; }
}
add_env ANKI_SYNC_TEST_USER "$TEST_USER:$(cat secrets/anki_sync_test_password)"
add_env MNEMOSYNE_APPROVE_TOKEN "$(cat secrets/approve_token)"
add_env MNEMOSYNE_PROPOSE_TOKEN "$(cat secrets/propose_token)"

user="$(grep -E '^ANKI_SYNC_USER=' "$HELIOS_ENV" | head -1 | cut -d= -f2- | cut -d: -f1)"
[ -f .env ] || cat > .env <<EOF
HELIOS_HOST_IP=$(grep -E '^HELIOS_HOST_IP=' "$HELIOS_ENV" | cut -d= -f2-)
HELIOS_LEDGER_DIR=$HOME/helios/stack/ledger
ANKI_SYNC_USERNAME=$user
ANKI_SYNC_TEST_USERNAME=$TEST_USER
EOF
echo "done. Tell Claude; it takes it from here."
