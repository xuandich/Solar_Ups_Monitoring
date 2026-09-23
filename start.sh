#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f "$SCRIPT_DIR/config.toml" ]]; then
    echo "Không tìm thấy config.toml. Hãy copy config.example.toml thành config.toml rồi chỉnh sửa trước." >&2
    exit 1
fi

exec /home/xuandich/.local/bin/uv run python main.py
