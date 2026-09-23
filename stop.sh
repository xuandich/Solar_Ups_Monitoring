#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/mqsolar.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "Không tìm thấy PID file, có thể service chưa chạy."
    exit 0
fi

PID="$(cat "$PID_FILE")"

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID không còn chạy, dọn PID file."
    rm -f "$PID_FILE"
    exit 0
fi

echo "Đang dừng MQ Solar service (PID $PID)..."
kill -TERM "$PID"

for _ in $(seq 1 10); do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "Đã dừng."
        exit 0
    fi
    sleep 1
done

echo "Service không dừng sau 10s, buộc kill -9..." >&2
kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
