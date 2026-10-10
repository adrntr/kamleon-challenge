#!/usr/bin/env bash
# Step-by-step demo of the API against a running stack (docker compose up --build).
#
# Usage: scripts/demo.sh
#   BASE_URL       API base URL (default http://localhost:8000)
#   DEMO_NO_PAUSE  set to 1 to run every step without waiting for Enter
#
# Every run registers a new customer and device, so it can be repeated on the same database.

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
RUN_ID="$(date +%s)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BOLD=$'\e[1m'
CYAN=$'\e[36m'
DIM=$'\e[2m'
RESET=$'\e[0m'

new_uuid() {
  if [[ -r /proc/sys/kernel/random/uuid ]]; then
    cat /proc/sys/kernel/random/uuid
  elif command -v uuidgen >/dev/null; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  else
    python3 -c 'import uuid; print(uuid.uuid4())'
  fi
}

step() {
  echo
  echo "${BOLD}${CYAN}== $1${RESET}"
  echo "${DIM}$2${RESET}"
  if [[ "${DEMO_NO_PAUSE:-0}" != "1" ]]; then
    read -r -p "Press Enter to run..." _
  fi
}

# call METHOD PATH [JSON_BODY]: prints the request, the status, the Location header and the body.
call() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -X "$method" -o "$TMP_DIR/body" -D "$TMP_DIR/headers" -w '%{http_code}')
  if [[ -n "$body" ]]; then
    args+=(-H 'Content-Type: application/json' -d "$body")
  fi
  echo "${DIM}\$ curl -X $method $BASE_URL$path${RESET}"
  if [[ -n "$body" ]]; then
    echo "${DIM}$body${RESET}"
  fi
  STATUS="$(curl "${args[@]}" "$BASE_URL$path")"
  LOCATION="$(grep -i '^location:' "$TMP_DIR/headers" | cut -d' ' -f2 | tr -d '\r' || true)"
  echo "${BOLD}HTTP $STATUS${RESET}${LOCATION:+  Location: $LOCATION}"
  if command -v python3 >/dev/null; then
    python3 -m json.tool --no-ensure-ascii "$TMP_DIR/body" || cat "$TMP_DIR/body"
  else
    cat "$TMP_DIR/body"
    echo
  fi
}

measurement() {
  local id="$1" type="$2" unit="$3" value="$4" timestamp="$5"
  printf '{"device_measurement_id": "%s", "type": "%s", "unit": "%s", "value": %s, "timestamp": "%s"}' \
    "$id" "$type" "$unit" "$value" "$timestamp"
}

M1="$(new_uuid)"
M2="$(new_uuid)"
M3="$(new_uuid)"
M4="$(new_uuid)"
M5="$(new_uuid)"
M6="$(new_uuid)"

FIRST_BATCH="{\"measurements\": [
  $(measurement "$M1" ph pH 7.41 2026-10-06T09:00:00+02:00),
  $(measurement "$M2" temperature degC 36.6 2026-10-06T09:00:00+02:00),
  $(measurement "$M3" current mA 1.25 2026-10-06T09:00:00+02:00)
]}"

MIXED_BATCH="{\"measurements\": [
  $(measurement "$M4" ph pH 7.38 2026-10-06T09:01:00+02:00),
  $(measurement "$M5" current MA 1.30 2026-10-06T09:01:00+02:00),
  $(measurement "$M1" ph pH 9.99 2026-10-06T09:00:00+02:00)
]}"

step "0. Readiness" "The API is up and the database answers SELECT 1."
call GET /health/ready

step "1. Register a customer" "The email is lowercased before storing. The id comes back in the Location header."
call POST /customers "{\"name\": \"Ana García\", \"email\": \"Ana.Demo$RUN_ID@Kamleon.com\"}"
CUSTOMER_PATH="$LOCATION"

step "2. Register a device for the customer" "Devices always start as active."
call POST "$CUSTOMER_PATH/devices" "{\"serial_number\": \"DEMO-$RUN_ID\"}"
DEVICE_PATH="$LOCATION"

step "3. Register the same serial again" "Plain INSERT; the unique constraint decides: 409 serial-already-registered (RFC 9457)."
call POST "$CUSTOMER_PATH/devices" "{\"serial_number\": \"DEMO-$RUN_ID\"}"

step "4. Ingest a batch of three measurements" "Each measurement carries its own device_measurement_id: 201, all created."
call POST "$DEVICE_PATH/measurements" "$FIRST_BATCH"

step "5. Retry the same batch" "Same ids, same payload: 201 again, reported as duplicate and not stored twice."
call POST "$DEVICE_PATH/measurements" "$FIRST_BATCH"

step "6. Mixed batch" "One new measurement, one unregistered (type, unit) pair ('current', 'MA'), and an existing id with a different value: 207 with one result per measurement."
call POST "$DEVICE_PATH/measurements" "$MIXED_BATCH"

step "7. Deactivate the device" "PATCH sets a target state; repeating it is a no-op that returns 200."
call PATCH "$DEVICE_PATH" '{"status": "inactive"}'

step "8. Ingest after deactivation" "The status is checked when the request arrives: 409 device-inactive, nothing stored."
call POST "$DEVICE_PATH/measurements" \
  "{\"measurements\": [$(measurement "$M6" ph pH 7.40 2026-10-06T09:02:00+02:00)]}"

step "9. Query the measurements" "Half-open range [from, to), oldest first, timestamps returned in UTC: the four stored measurements."
call GET "$DEVICE_PATH/measurements?from=2026-10-06T00:00:00Z&to=2026-10-07T00:00:00Z"

echo
echo "${BOLD}Demo finished.${RESET}"
