#!/usr/bin/env python3
"""幂等注入 Caddy 上 opencode relay 的路由。

为什么走 admin API
------------------
`/etc/caddy/Caddyfile` 是 root 拥有、这个账号只读，改不了。而 admin API 的改动
**只存在内存里**，Caddy 一重启路由就没了 —— 所以必须配看门狗定时重灌
（见 relay-route-watch.sh）。

路由形状
--------
手机 App 和电脑端桥接都用「基址 + /ws/<房间>/mobile」拼地址，所以这里把
`/opencode-relay/*` 前缀剥掉再转给 127.0.0.1:8765：

    wss://43.143.76.55/opencode-relay  →  http://127.0.0.1:8765

上游必须固定 HTTP/1.1：relay 是 uvicorn 的 WebSocket，h2 握不上
（同 Caddyfile 里 DSH 那条注释的教训）。

用法:
    python3 ensure-relay-route.py            # 装路由
    python3 ensure-relay-route.py --check    # 只体检
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ADMIN = os.environ.get("CADDY_ADMIN", "http://127.0.0.1:2019")
SERVER = os.environ.get("CADDY_SERVER", "srv1")     # srv1 = :443（主站）
ROUTE_PATH = os.environ.get("RELAY_ROUTE_PATH", "/opencode-relay")
RELAY_UPSTREAM = os.environ.get("RELAY_UPSTREAM", "127.0.0.1:8765")
HOST = os.environ.get("RELAY_PUBLIC_HOST", "43.143.76.55")


def admin_get(path):
    with urllib.request.urlopen(ADMIN + path, timeout=10) as resp:
        return json.loads(resp.read())


def admin_post_config(cfg):
    req = urllib.request.Request(
        ADMIN + "/config/",
        data=json.dumps(cfg).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def build_route():
    """与 Caddyfile 里 handle_path 等价的 JSON 形态。"""
    return {
        "match": [{"host": [HOST], "path": [ROUTE_PATH, ROUTE_PATH + "/*"]}],
        "handle": [{
            "handler": "subroute",
            "routes": [
                {"handle": [{"handler": "rewrite", "strip_path_prefix": ROUTE_PATH}]},
                {"handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": RELAY_UPSTREAM}],
                    "transport": {"protocol": "http", "versions": ["1.1"]},
                }]},
            ],
        }],
        "terminal": True,
    }


def is_our_route(route):
    return ROUTE_PATH in json.dumps(route)


def route_is_current(routes):
    """路由存在、在最前面、且指向上游。"""
    ours = [r for r in routes if is_our_route(r)]
    if len(ours) != 1:
        return False
    if not is_our_route(routes[0]):
        return False
    return RELAY_UPSTREAM in json.dumps(routes[0])


def install():
    cfg = admin_get("/config/")
    servers = cfg["apps"]["http"]["servers"]
    if SERVER not in servers:
        print(f"  ! Caddy 里没有 {SERVER}，跳过")
        return False

    routes = servers[SERVER].get("routes", [])
    n_ours = sum(1 for r in routes if is_our_route(r))
    if n_ours > 1:
        print(f"  ! 发现 {n_ours} 条重复路由，清理中")
    if route_is_current(routes):
        print("  ✓ 路由已就位")
        return True

    # 新的放最前面：后面的 catch-all 会把请求吞掉
    others = [r for r in routes if not is_our_route(r)]
    servers[SERVER]["routes"] = [build_route()] + others
    admin_post_config(cfg)
    print("  ✓ 已注入路由")
    return True


def check():
    try:
        cfg = admin_get("/config/")
        routes = cfg["apps"]["http"]["servers"][SERVER].get("routes", [])
        n_ours = sum(1 for r in routes if is_our_route(r))
        if route_is_current(routes):
            print("  ✓ 路由已就位")
        else:
            print(f"  ! 路由缺失或重复（{n_ours} 条）—— 需要重灌")
            return False
    except Exception as exc:
        print(f"  ! 读取 Caddy 配置失败: {exc}")
        return False

    import urllib.request as u
    try:
        with u.urlopen(f"http://{RELAY_UPSTREAM}/api/stats", timeout=5) as resp:
            body = resp.read(200).decode("utf-8", "ignore")
        print(f"  ✓ relay 存活: {body}")
    except Exception as exc:
        print(f"  ! relay 没响应: {exc}")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="确保 Caddy 上的 opencode relay 路由存在")
    ap.add_argument("--check", action="store_true", help="只检查不修改")
    args = ap.parse_args()

    if args.check:
        print("检查 relay 路由：")
        sys.exit(0 if check() else 1)

    print("注入 relay 路由：")
    try:
        install()
    except Exception as exc:
        print(f"  ! 失败: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
