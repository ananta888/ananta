#!/bin/sh
set -eu
umask 077

: "${TURN_REALM:?TURN_REALM is required}"
: "${TURN_SERVER_NAME:?TURN_SERVER_NAME is required}"
: "${TURN_EXTERNAL_IP:?TURN_EXTERNAL_IP is required}"
: "${TURN_MIN_PORT:?TURN_MIN_PORT is required}"
: "${TURN_MAX_PORT:?TURN_MAX_PORT is required}"

case "$TURN_REALM$TURN_SERVER_NAME$TURN_EXTERNAL_IP$TURN_MIN_PORT$TURN_MAX_PORT" in
  *[!A-Za-z0-9._:-]*) echo "invalid TURN runtime configuration" >&2; exit 64 ;;
esac
case "$TURN_MIN_PORT:$TURN_MAX_PORT" in
  *[!0-9:]*) echo "invalid TURN relay port range" >&2; exit 64 ;;
esac
if [ "$TURN_MIN_PORT" -lt 1024 ] || [ "$TURN_MAX_PORT" -gt 65535 ] \
  || [ "$TURN_MIN_PORT" -gt "$TURN_MAX_PORT" ] \
  || [ $((TURN_MAX_PORT - TURN_MIN_PORT)) -gt 1000 ]; then
  echo "invalid TURN relay port range" >&2
  exit 64
fi

secret="$(cat /run/secrets/turn_rest_auth_secret)"
case "$secret" in
  ""|*[!A-Za-z0-9_+=./-]*) echo "invalid TURN auth secret format" >&2; exit 78 ;;
esac
if [ "${#secret}" -lt 32 ]; then
  echo "TURN auth secret is too short" >&2
  exit 78
fi

runtime_config=/run/turnserver.conf
sed \
  -e "s/__TURN_REALM__/$TURN_REALM/g" \
  -e "s/__TURN_SERVER_NAME__/$TURN_SERVER_NAME/g" \
  -e "s/__TURN_EXTERNAL_IP__/$TURN_EXTERNAL_IP/g" \
  -e "s/__TURN_MIN_PORT__/$TURN_MIN_PORT/g" \
  -e "s/__TURN_MAX_PORT__/$TURN_MAX_PORT/g" \
  /etc/coturn/turnserver.conf.template >"$runtime_config"
printf 'static-auth-secret=%s\n' "$secret" >>"$runtime_config"
unset secret

exec turnserver -c "$runtime_config"
