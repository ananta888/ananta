#!/bin/sh
set -eu

required_rmem="${ANANTA_SFU_SOCKET_RMEM_MIN:-16777216}"
required_wmem="${ANANTA_SFU_SOCKET_WMEM_MIN:-16777216}"
required_conntrack="${ANANTA_SFU_CONNTRACK_MIN:-262144}"
required_nofile="${ANANTA_SFU_HOST_NOFILE_MIN:-1048576}"
port_min="${ANANTA_SFU_UDP_PORT_MIN:-50000}"
port_max="${ANANTA_SFU_UDP_PORT_MAX:-60000}"
firewall_marker="${ANANTA_SFU_FIREWALL_MARKER:-ananta_sfu}"
status=go
reasons=""

fail_check() {
  status=unsupported
  reasons="${reasons}${reasons:+,}\"$1\""
}

[ -r /sys/fs/cgroup/cgroup.controllers ] || fail_check cgroup_v2_unavailable
for controller in cpu memory pids; do
  grep -qw "$controller" /sys/fs/cgroup/cgroup.controllers 2>/dev/null || fail_check "cgroup_${controller}_unavailable"
done

rmem="$(sysctl -n net.core.rmem_max 2>/dev/null || printf 0)"
wmem="$(sysctl -n net.core.wmem_max 2>/dev/null || printf 0)"
conntrack="$(sysctl -n net.netfilter.nf_conntrack_max 2>/dev/null || printf 0)"
[ "$rmem" -ge "$required_rmem" ] || fail_check socket_receive_buffer_insufficient
[ "$wmem" -ge "$required_wmem" ] || fail_check socket_send_buffer_insufficient
[ "$conntrack" -ge "$required_conntrack" ] || fail_check conntrack_limit_insufficient
[ "$(ulimit -n)" -ge "$required_nofile" ] || fail_check host_nofile_insufficient

case "$port_min:$port_max" in *[!0-9:]*) fail_check udp_port_range_invalid ;; esac
[ "$port_min" -ge 1024 ] && [ "$port_min" -le "$port_max" ] && [ "$port_max" -le 65535 ] || fail_check udp_port_range_invalid

if command -v ss >/dev/null 2>&1; then
  if ss -H -l -u -n | awk -v low="$port_min" -v high="$port_max" '
    { value=$5; sub(/^.*:/,"",value); if (value+0 >= low && value+0 <= high) found=1 }
    END { exit found ? 0 : 1 }
  '; then
    fail_check udp_port_range_in_use
  fi
else
  fail_check socket_inventory_unavailable
fi

if command -v nft >/dev/null 2>&1; then
  nft list ruleset 2>/dev/null | grep -q "$firewall_marker" || fail_check firewall_policy_unverified
else
  fail_check firewall_enforcer_unavailable
fi

if command -v bpftool >/dev/null 2>&1; then
  bpftool prog show 2>/dev/null | grep -q ananta_sfu || fail_check packet_rate_enforcer_unverified
else
  fail_check packet_rate_enforcer_unavailable
fi

printf '{"schema_version":"sfu_runtime_preflight.v1","status":"%s","reason_codes":[%s]}\n' "$status" "$reasons"
[ "$status" = go ]

