#!/bin/sh
# Optional runtime CA bundle: mount host ./certs here (see docker-compose.yml).
set -e
if [ -d /usr/local/share/ca-certificates/corp-mount ]; then
  for f in /usr/local/share/ca-certificates/corp-mount/*; do
    [ -f "$f" ] || continue
    case "$f" in
      *.crt|*.pem)
        bn=$(basename "$f")
        cp "$f" "/usr/local/share/ca-certificates/corp-mount-${bn}.crt"
        ;;
    esac
  done
  if ls /usr/local/share/ca-certificates/corp-mount-*.crt >/dev/null 2>&1; then
    update-ca-certificates
  fi
fi
exec "$@"
