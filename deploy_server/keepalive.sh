#!/usr/bin/env bash
#
# opencode-android-remote 的服务端保活。
#
# 三个长驻进程 + 一条 Caddy 路由，任何一个没了都拉起来：
#
#   opencode serve   127.0.0.1:4096   真正的 Agent（opencode 1.18.x）
#   relay server     127.0.0.1:8765   手机 ↔ 电脑的中继
#   desktop bridge   连 relay          把手机的指令转给本地 opencode
#   pair loop        连 relay          不断发一次性配对码（TTL 只有 120 秒）
#   Caddy 路由       /opencode-relay/* → 127.0.0.1:8765（admin API 注入，不落盘）
#
# 幂等：重复执行只会补上缺的那个。
#
# 用法：bash keepalive.sh          # 检查并修复
#       bash keepalive.sh --report # 只体检
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STATE="${STATE:-$HOME/.local/share/opencode-remote}"
PUB="${PUB:-$HOME/.local/share/kimi-apk-dist/opencode}"
VENV="$ROOT/.venv"
OPENCODE_BIN="${OPENCODE_BIN:-$HOME/toolchains/opencode/node_modules/.bin/opencode}"
RELAY_URL="${RELAY_SERVER_URL:-wss://43.143.76.55/opencode-relay}"
ACCOUNT="${OPENCODE_ACCOUNT_ID:-kestrel}"
REPORT_ONLY=0

[ "${1:-}" = "--report" ] && REPORT_ONLY=1

log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '  \033[32mOK\033[0m %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; }

alive() { pgrep -f "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- 环境
set -a; . "$STATE/relay.env" 2>/dev/null || true; . "$STATE/env.sh" 2>/dev/null || true; set +a
export RELAY_SERVER_URL="$RELAY_URL" OPENCODE_ACCOUNT_ID="$ACCOUNT"
export OPENCODE_SERVER_URL="${OPENCODE_SERVER_URL:-http://127.0.0.1:4096}"
export AGENT_FILE_ROOTS="${AGENT_FILE_ROOTS:-/home/kestrel/workspace}"

# ---------------------------------------------------------------- 1. opencode
if alive "opencode serve --port 4096"; then
  ok "opencode serve 在跑"
elif [ "$REPORT_ONLY" -eq 1 ]; then
  bad "opencode serve 没跑"
else
  log "拉起 opencode serve…"
  setsid nohup env MINIMAX_API_KEY="${MINIMAX_API_KEY:-}" "$OPENCODE_BIN" serve --port 4096 --hostname 127.0.0.1 \
    > "$STATE/opencode.log" 2>&1 < /dev/null &
  sleep 6
  alive "opencode serve --port 4096" && ok "opencode serve 已启动" || bad "opencode serve 启动失败，看 $STATE/opencode.log"
fi

# ---------------------------------------------------------------- 2. relay
if alive "python.*server.py"; then
  ok "relay 在跑"
elif [ "$REPORT_ONLY" -eq 1 ]; then
  bad "relay 没跑"
else
  log "拉起 relay…"
  cd "$ROOT/relay_server" || exit 1
  setsid nohup env HOST=127.0.0.1 PORT=8765 RELAY_ADMIN_TOKEN="${RELAY_ADMIN_TOKEN:-}" \
    "$VENV/bin/python" -u server.py > "$STATE/relay.log" 2>&1 < /dev/null &
  sleep 4
  alive "python.*server.py" && ok "relay 已启动" || bad "relay 启动失败，看 $STATE/relay.log"
fi

# ---------------------------------------------------------------- 3. 桌面桥接
if alive "agent.py run"; then
  ok "桌面桥接在跑"
elif [ "$REPORT_ONLY" -eq 1 ]; then
  bad "桌面桥接没跑"
else
  log "拉起桌面桥接…"
  setsid nohup "$VENV/bin/python" -u "$ROOT/desktop_agent/agent.py" run \
    > "$STATE/agent.log" 2>&1 < /dev/null &
  sleep 8
  alive "agent.py run" && ok "桌面桥接已启动" || bad "桥接启动失败，看 $STATE/agent.log"
fi

# ---------------------------------------------------------------- 4. 配对码循环
if alive "pair-loop.sh"; then
  ok "配对码循环在跑"
elif [ "$REPORT_ONLY" -eq 1 ]; then
  bad "配对码循环没跑"
else
  log "拉起配对码循环…"
  setsid nohup bash "$HERE/pair-loop.sh" > "$STATE/pair-loop.log" 2>&1 < /dev/null &
  sleep 3
  alive "pair-loop.sh" && ok "配对码循环已启动" || bad "配对码循环启动失败"
fi

# ---------------------------------------------------------------- 5. Caddy 路由
route_out=$(python3 "$HERE/ensure-relay-route.py" --check 2>&1)
if [ $? -eq 0 ]; then
  echo "$route_out" | sed 's/^/  /'
else
  echo "$route_out" | sed 's/^/  /'
  if [ "$REPORT_ONLY" -eq 1 ]; then
    bad "Caddy 路由缺失，需要重灌"
  else
    log "重灌 Caddy 路由…"
    python3 "$HERE/ensure-relay-route.py" | sed 's/^/  /'
  fi
fi

# ---------------------------------------------------------------- 6. 配对链接
if [ -s "$PUB/pair-link.txt" ]; then
  ok "配对链接：$PUB/pair-link.txt"
  ok "二维码：  $PUB/pair-qr.png"
else
  warn "还没有配对链接（配对码循环刚起来，稍等一轮）"
fi

exit 0
