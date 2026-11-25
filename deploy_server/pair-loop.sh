#!/usr/bin/env bash
#
# 反复向 relay 申请一次性配对码，把最新的配对链接和二维码写到公开目录。
#
# 配对码一次性、TTL 只有 120 秒，手机不一定赶得上。所以这里常驻循环：
# 每轮生成一个新码并覆盖公开文件，用户什么时候想连就扫最新的那张。
#
# 依赖：desktop_agent 的依赖已装好（venv 在仓库根的 .venv）。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STATE="${STATE:-$HOME/.local/share/opencode-remote}"
PUB="${PUB:-$HOME/.local/share/kimi-apk-dist/opencode}"
RELAY_URL="${RELAY_SERVER_URL:-wss://43.143.76.55/opencode-relay}"
ACCOUNT="${OPENCODE_ACCOUNT_ID:-kestrel}"
INTERVAL="${INTERVAL:-30}"

mkdir -p "$STATE" "$PUB"

while true; do
  set -a; . "$STATE/relay.env" 2>/dev/null || true; set +a
  export RELAY_SERVER_URL="$RELAY_URL" OPENCODE_ACCOUNT_ID="$ACCOUNT"
  export OPENCODE_SERVER_URL="${OPENCODE_SERVER_URL:-http://127.0.0.1:4096}"
  export AGENT_FILE_ROOTS="${AGENT_FILE_ROOTS:-/home/kestrel/workspace}"

  # -u 必须加：管道下 Python 是块缓冲，进程被 timeout 杀掉时缓冲区里的
  # 配对链接会丢（只看到二维码）
  out=$(timeout 100 "$ROOT/.venv/bin/python" -u "$ROOT/desktop_agent/agent.py" pair 2>&1)
  link=$(printf '%s\n' "$out" | grep -oE "opencode-remote://pair\?[^ ]+" | head -1)
  if [ -n "$link" ]; then
    printf '%s\n' "$link" > "$STATE/pair-link.txt"
    printf '%s\n' "$link" > "$PUB/pair-link.txt"
    "$ROOT/.venv/bin/python" - "$link" "$PUB/pair-qr.png" <<'PY'
import sys
import qrcode
qr = qrcode.QRCode(border=2)
qr.add_data(sys.argv[1])
qr.make()
qr.make_image().save(sys.argv[2])
PY
    echo "[$(date +%H:%M:%S)] 新配对码已生成 → $PUB/pair-qr.png"
  else
    echo "[$(date +%H:%M:%S)] 配对码申请失败：$(printf '%s\n' "$out" | tail -2)"
  fi
  sleep "$INTERVAL"
done
