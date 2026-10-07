#!/usr/bin/env bash
# Make a self-signed TLS certificate for a LAN install, into deploy/certs/.
# Good enough for an internal, air-gapped deployment where the bank has no
# internal CA yet; replace kpigo.crt/kpigo.key with their CA-issued pair when
# they do (same filenames, no rebuild).
#
#   scripts/gen_selfsigned.sh [hostname]      # default hostname: kpigo.local
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
certs="${here}/deploy/certs"
host="${1:-kpigo.local}"
mkdir -p "$certs"

if [ -f "${certs}/kpigo.crt" ] && [ -f "${certs}/kpigo.key" ]; then
  echo "Certificate already present in ${certs}. Remove it first to regenerate." >&2
  exit 0
fi

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout "${certs}/kpigo.key" -out "${certs}/kpigo.crt" \
  -subj "/CN=${host}" \
  -addext "subjectAltName=DNS:${host},DNS:localhost,IP:127.0.0.1"
chmod 600 "${certs}/kpigo.key"
echo "Self-signed certificate for ${host} written to ${certs}."
echo "Bring the stack up with: docker compose -f compose.yaml -f compose.tls.yaml up -d"
