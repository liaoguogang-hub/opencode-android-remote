#!/usr/bin/env python3
"""常驻配对码服务：一条连接反复发放配对码，同时随时接住认领。

为什么要常驻
------------
pairing token 是 relay 内存里的一次性凭证，只属于**申请它的那条连接**，且
兑换（pair_claim）必须赶在 120 秒 TTL 内。早先的实现是每轮跑一遍 `agent.py pair`：
拿到码、打印、等 120 秒、退出、再发新码。手机点开的链接往往已经是上一轮留下的
过期码，兑换必然失败 —— 表现为「扫码后说配对失败 / 一直连不上」，relay 日志里
则是 `pair_claim` 被拒。

这里改成一条长连接：
  * 每 `--refresh` 秒（默认 30）申请一张新码，写 pair-link.txt / pair-qr.png；
  * 同一个循环里持续读帧，`ping` 回 `pong`（不回会被 relay 判僵尸连接踢掉），
    `device_paired` 记日志。

用法（一般由 pair-loop.sh 调起）:
    pair-daemon.py --relay wss://host/opencode-relay --account kestrel \
                   --state ~/.local/share/opencode-remote \
                   --pub   ~/.local/share/kimi-apk-dist/opencode
"""
import argparse
import asyncio
import json
import os
import socket
import sys
import time
import urllib.parse
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop_agent"))

from modules import config  # noqa: E402

# 必须在 import 之后、使用之前改：config 在模块加载时就把路径算好了，
# 之后再设环境变量不生效。用独立文件，才不会和常驻桥接共用 device_id
# 互相踢下线（relay 同 id 的新连接会顶掉旧的）。
if os.getenv("OPENCODE_DEVICE_ID_FILE"):
    config._DESKTOP_DEVICE_ID_FILE = os.environ["OPENCODE_DEVICE_ID_FILE"]
from modules.keystore import get_or_create_secret  # noqa: E402
from modules.protocol import do_hello_handshake  # noqa: E402


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_artifacts(state: Path, pub: Path, link: str) -> None:
    """把配对链接与二维码写到两处（状态目录 + 公网目录）。"""
    for d in (state, pub):
        d.mkdir(parents=True, exist_ok=True)
        (d / "pair-link.txt").write_text(link + "\n", encoding="utf-8")
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(link)
        qr.make()
        qr.make_image().save(pub / "pair-qr.png")
    except Exception as exc:  # 没装 Pillow/qrcode 时只丢二维码，不影响链接
        log(f"二维码生成跳过: {exc}")


async def run(args) -> None:
    secret = get_or_create_secret()
    if not secret:
        log("没有主 secret，先跑一次 agent.py pair 生成")
        return
    endpoint = f"{args.relay.rstrip('/')}/ws/{args.account}/desktop"
    desktop_name = socket.gethostname()

    while True:
        try:
            log(f"连接 {endpoint}")
            async with websockets.connect(endpoint, ping_interval=20, ping_timeout=10) as ws:
                await do_hello_handshake(ws)
                auth = {
                    "type": "auth",
                    "account_id": args.account,
                    "secret": secret,
                    "client_type": "desktop",
                    "device_id": config.get_desktop_device_id(),
                }
                if config.RELAY_ADMIN_TOKEN:
                    auth["admin_token"] = config.RELAY_ADMIN_TOKEN
                await ws.send(json.dumps(auth))

                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if resp.get("type") == "auth_error":
                    log(f"鉴权失败: {resp.get('message')}")
                    await asyncio.sleep(10)
                    continue
                log(f"已连上 relay（device_id={config.get_desktop_device_id()}）")

                async def refresh_loop():
                    while True:
                        try:
                            await ws.send(json.dumps({
                                "type": "create_pairing",
                                "desktop_name": desktop_name,
                            }))
                        except Exception:
                            return
                        await asyncio.sleep(args.refresh)

                refresher = asyncio.create_task(refresh_loop())
                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        t = msg.get("type")
                        if t == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
                        elif t == "pairing_created":
                            token = msg.get("pairing_token", "")
                            if not token:
                                continue
                            link = "opencode-remote://pair?" + urllib.parse.urlencode({
                                "relay": args.relay,
                                "account": args.account,
                                "token": token,
                                "name": desktop_name,
                            })
                            write_artifacts(args.state, args.pub, link)
                            log(f"新配对码 {token[:8]}… → {args.pub}/pair-qr.png "
                                f"(ttl={msg.get('ttl_seconds')}s)")
                        elif t == "device_paired":
                            log(f"设备已配对: {msg.get('device_name')} "
                                f"id={msg.get('device_id', '')[:8]}…")
                        elif t == "pair_error":
                            log(f"配对失败: {msg.get('message')}")
                finally:
                    refresher.cancel()
                    log(f"连接结束：主循环退出（ws 关闭码={ws.close_code} {ws.close_reason}）")
        except Exception as exc:
            log(f"连接异常: {type(exc).__name__}: {exc}")
        log("5 秒后重连…")
        await asyncio.sleep(5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relay", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--pub", required=True, type=Path)
    ap.add_argument("--refresh", type=int, default=30)
    args = ap.parse_args()
    os.makedirs(args.state, exist_ok=True)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
