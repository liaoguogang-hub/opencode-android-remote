#!/usr/bin/env bash
#
# 配对码服务：常驻一个 desktop 连接，不断申请一次性配对码，把最新的链接和二维码
# 写到公开目录。
#
# 为什么不能「发完码就断开」
# ------------------------
# 配对码是 relay 内存里的一次性凭证，只有**发码的那个连接**知道它，而且
# 兑换（pair_claim）必须发生在码还有效时。早先的实现是每次跑一遍 `agent.py pair`
# —— 它在拿到码、打印出来后等 120 秒就退出，然后重新发一张新码。手机上拿到的
# 链接往往已经过期或者指向一张早被作废的码，兑换必然失败。
#
# 现在的做法：常驻一条连接，每 30 秒提一张新码写文件，同时**持续监听** device_paired。
# 手机扫到哪张都能兑换成功。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STATE="${STATE:-$HOME/.local/share/opencode-remote}"
PUB="${PUB:-$HOME/.local/share/kimi-apk-dist/opencode}"
RELAY_URL="${RELAY_SERVER_URL:-wss://43.143.76.55/opencode-relay}"
ACCOUNT="${OPENCODE_ACCOUNT_ID:-kestrel}"

mkdir -p "$STATE" "$PUB"

# 与常驻桥接分开的 device_id，否则 relay 会把桥接顶下线
export OPENCODE_DEVICE_ID_FILE="$STATE/.pairing_device_id"
export RELAY_SERVER_URL="$RELAY_URL"
export OPENCODE_ACCOUNT_ID="$ACCOUNT"
export OPENCODE_SERVER_URL="${OPENCODE_SERVER_URL:-http://127.0.0.1:4096}"
export AGENT_FILE_ROOTS="${AGENT_FILE_ROOTS:-/home/kestrel/workspace}"
set -a; . "$STATE/relay.env" 2>/dev/null || true; set +a

exec "$ROOT/.venv/bin/python" -u "$HERE/pair-daemon.py" \
  --relay "$RELAY_URL" --account "$ACCOUNT" --state "$STATE" --pub "$PUB"
