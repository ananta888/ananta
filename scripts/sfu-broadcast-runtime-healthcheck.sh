#!/bin/sh
set -eu

memory_stop_percent="${ANANTA_SFU_MEMORY_STOP_PERCENT:-85}"
pids_stop_percent="${ANANTA_SFU_PIDS_STOP_PERCENT:-85}"
fd_stop_percent="${ANANTA_SFU_FD_STOP_PERCENT:-85}"

ratio_below() {
  current="$1"
  maximum="$2"
  threshold="$3"
  [ "$maximum" != "max" ] || return 1
  [ "$maximum" -gt 0 ] || return 1
  [ $((current * 100)) -lt $((maximum * threshold)) ]
}

if [ -r /sys/fs/cgroup/memory.current ] && [ -r /sys/fs/cgroup/memory.max ]; then
  ratio_below "$(cat /sys/fs/cgroup/memory.current)" "$(cat /sys/fs/cgroup/memory.max)" "$memory_stop_percent" || exit 1
else
  exit 1
fi

if [ -r /sys/fs/cgroup/pids.current ] && [ -r /sys/fs/cgroup/pids.max ]; then
  ratio_below "$(cat /sys/fs/cgroup/pids.current)" "$(cat /sys/fs/cgroup/pids.max)" "$pids_stop_percent" || exit 1
else
  exit 1
fi

fd_max="$(awk '$1 == "Max" && $2 == "open" {print $(NF-2)}' /proc/1/limits)"
fd_current="$(find /proc/1/fd -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"
[ -n "$fd_max" ] && ratio_below "$fd_current" "$fd_max" "$fd_stop_percent" || exit 1

core_limit="$(awk '$1 == "Max" && $2 == "core" {print $(NF-2)}' /proc/1/limits)"
[ "$core_limit" = "0" ] || exit 1

if [ -n "${ANANTA_SFU_HEALTH_URL:-}" ]; then
  wget -q -T 2 --spider "$ANANTA_SFU_HEALTH_URL" || exit 1
fi
