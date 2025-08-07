#!/bin/bash

# Dieses Skript wartet darauf, dass ein Host und Port verfügbar werden

COMMAND='wait-for-it.sh'
USAGE="Verwendung: $COMMAND [-q] [-t timeout] host:port [-- command args]\n -q: Quiet-Modus\n -t timeout: Timeout in Sekunden (Standard: 15)\n -- command args: Befehl, der ausgeführt werden soll, nachdem der Host verfügbar ist"

# Parse-Parameter
quiet=0
timeout=15

while [[ $# -gt 0 ]]
do
  case "$1" in
    -q)
    quiet=1
    shift 1
    ;;
    -t)
    timeout="$2"
    if [[ -z "$timeout" ]]; then
      echo "Fehler: -t erfordert einen Timeout-Wert" >&2
      exit 1
    fi
    shift 2
    ;;
    --)
    shift
    break
    ;;
    -*)
    echo "Fehler: Unbekannte Option: $1" >&2
    echo "$USAGE" >&2
    exit 1
    ;;
    *)
    break
    ;;
  esac
done

hostport=$1
shift

if [[ -z "$hostport" ]]; then
  echo "Fehler: Host:Port erforderlich" >&2
  echo "$USAGE" >&2
  exit 1
fi

host=$(echo $hostport | cut -d: -f1)
port=$(echo $hostport | cut -d: -f2)

if [[ -z "$port" ]]; then
  echo "Fehler: Bitte einen Host und Port angeben: $hostport" >&2
  exit 1
fi

# Funktion zum Überprüfen der Verbindung
wait_for() {
  if [[ $quiet -ne 1 ]]; then
    echo "Warte auf $host:$port..."
  fi

  start_ts=$(date +%s)
  while :
  do
    (echo > /dev/tcp/$host/$port) >/dev/null 2>&1
    result=$?
    if [[ $result -eq 0 ]]; then
      if [[ $quiet -ne 1 ]]; then
        end_ts=$(date +%s)
        echo "$host:$port ist verfügbar nach $((end_ts - start_ts)) Sekunden"
      fi
      break
    fi

    current_ts=$(date +%s)
    if [[ $((current_ts - start_ts)) -gt $timeout ]]; then
      if [[ $quiet -ne 1 ]]; then
        echo "Timeout beim Warten auf $host:$port nach $timeout Sekunden"
      fi
      return 1
    fi

    if [[ $quiet -ne 1 ]]; then
      echo "$host:$port ist noch nicht verfügbar..."
    fi
    sleep 1
  done
  return 0
}

wait_for
WAIT_RESULT=$?

if [[ $WAIT_RESULT -ne 0 ]]; then
  echo "Timeout beim Warten auf $host:$port" >&2
  exit 1
fi

# Führe den Befehl aus, wenn angegeben
if [[ $# -gt 0 ]]; then
  exec "$@"
fi
