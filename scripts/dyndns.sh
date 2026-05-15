#!/bin/bash
# DynDNS update script for minipc.ananta.de
CONFIG="/home/krusty/ananta/scripts/dyndns.conf"
if [ -f "$CONFIG" ]; then
    source "$CONFIG"
fi
URL="${DYNDNS_URL:-https://onehome.dogado.de/dynDns/update?login=minipc.ananta.de&password=YJb4SpfcJfxy9yyN}"
curl -s --connect-timeout 10 --max-time 15 "$URL"
