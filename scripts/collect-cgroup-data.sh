#!/usr/bin/env bash
#
# collect.sh — Polls cgroup files for a container and writes CSV.
#
# Usage: ./collect.sh <container-id> [interval_seconds] [output_file]
#
# Finds the cgroup path for the given container ID and samples:
#   - memory.current (bytes)
#   - memory.high (bytes or "max")
#   - memory.max (bytes or "max")
#   - memory.events (low, high, max, oom, oom_kill counters)
#   - memory.stat (anon, file, pgfault, pgmajfault)
#
# Outputs CSV to stdout and optionally to a file.

set -euo pipefail

CONTAINER_ID="${1:?Usage: $0 <container-id> [interval] [output_file]}"
INTERVAL="${2:-0.5}"
OUTPUT="${3:-/dev/stdout}"

# Find the cgroup path for this container
find_cgroup_path() {
    local cid="$1"
    # Try systemd slice naming (most common)
    local path
    path=$(find /sys/fs/cgroup/kubepods.slice -name "*${cid}*" -type d 2>/dev/null | head -1)
    if [[ -z "$path" ]]; then
        # Try cgroupfs naming
        path=$(find /sys/fs/cgroup -path "*${cid}*" -name "memory.current" 2>/dev/null | head -1)
        path=$(dirname "$path" 2>/dev/null || true)
    fi
    if [[ -z "$path" || ! -f "$path/memory.current" ]]; then
        echo "ERROR: Cannot find cgroup for container $cid" >&2
        exit 1
    fi
    echo "$path"
}

CGROUP_PATH=$(find_cgroup_path "$CONTAINER_ID")
echo "Collecting from: $CGROUP_PATH" >&2
echo "Interval: ${INTERVAL}s" >&2

# Also find the pod-level cgroup (parent)
POD_CGROUP=$(dirname "$CGROUP_PATH")
echo "Pod cgroup: $POD_CGROUP" >&2

# CSV header
HEADER="timestamp_s,elapsed_s,memory_current_bytes,memory_high_bytes,memory_max_bytes,evt_low,evt_high,evt_max,evt_oom,evt_oom_kill,anon_bytes,file_bytes,pgfault,pgmajfault,pod_memory_min_bytes"
echo "$HEADER" | tee "$OUTPUT"

START=$(date +%s.%N)

read_val() {
    local val
    val=$(cat "$1" 2>/dev/null || echo "0")
    if [[ "$val" == "max" ]]; then
        echo "0"  # sentinel: 0 means unlimited in our CSV
    else
        echo "$val"
    fi
}

read_event() {
    # Parse "key value" lines from memory.events
    local file="$1" key="$2"
    awk -v k="$key" '$1 == k {print $2}' "$file" 2>/dev/null || echo "0"
}

read_stat() {
    local file="$1" key="$2"
    awk -v k="$key" '$1 == k {print $2}' "$file" 2>/dev/null || echo "0"
}

while [[ -f "$CGROUP_PATH/memory.current" ]]; do
    NOW=$(date +%s.%N)
    ELAPSED=$(echo "$NOW - $START" | bc)

    MEM_CURRENT=$(read_val "$CGROUP_PATH/memory.current")
    MEM_HIGH=$(read_val "$CGROUP_PATH/memory.high")
    MEM_MAX=$(read_val "$CGROUP_PATH/memory.max")

    EVT_LOW=$(read_event "$CGROUP_PATH/memory.events" "low")
    EVT_HIGH=$(read_event "$CGROUP_PATH/memory.events" "high")
    EVT_MAX=$(read_event "$CGROUP_PATH/memory.events" "max")
    EVT_OOM=$(read_event "$CGROUP_PATH/memory.events" "oom")
    EVT_OOM_KILL=$(read_event "$CGROUP_PATH/memory.events" "oom_kill")

    ANON=$(read_stat "$CGROUP_PATH/memory.stat" "anon")
    FILE=$(read_stat "$CGROUP_PATH/memory.stat" "file")
    PGFAULT=$(read_stat "$CGROUP_PATH/memory.stat" "pgfault")
    PGMAJFAULT=$(read_stat "$CGROUP_PATH/memory.stat" "pgmajfault")

    POD_MEM_MIN=$(read_val "$POD_CGROUP/memory.min")

    LINE="$NOW,$ELAPSED,$MEM_CURRENT,$MEM_HIGH,$MEM_MAX,$EVT_LOW,$EVT_HIGH,$EVT_MAX,$EVT_OOM,$EVT_OOM_KILL,$ANON,$FILE,$PGFAULT,$PGMAJFAULT,$POD_MEM_MIN"

    if [[ "$OUTPUT" != "/dev/stdout" ]]; then
        echo "$LINE" >> "$OUTPUT"
        # Also print a summary to stderr every 5 seconds
        if (( $(echo "$ELAPSED % 5 < $INTERVAL" | bc -l) )); then
            printf "\r[%6.1fs] mem=%dMi high=%dMi max=%dMi evt_high=%d evt_oom_kill=%d" \
                "$ELAPSED" \
                "$((MEM_CURRENT / 1048576))" \
                "$((MEM_HIGH / 1048576))" \
                "$((MEM_MAX / 1048576))" \
                "$EVT_HIGH" \
                "$EVT_OOM_KILL" >&2
        fi
    else
        echo "$LINE"
    fi

    sleep "$INTERVAL"
done

echo "" >&2
echo "Container cgroup gone — collection stopped at elapsed=${ELAPSED}s" >&2
