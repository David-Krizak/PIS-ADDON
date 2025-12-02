#!/usr/bin/with-contenv bash
set -e

echo "[INFO] Starting PIS Electricity Meter add-on"

cd /usr/src/app
exec python -m pis_pis_meter.app
