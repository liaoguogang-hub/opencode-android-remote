#!/usr/bin/env python3
"""把 opencode 的 Web UI（127.0.0.1:4096）暴露到公网。

`opencode serve` 的根路径就是完整的 Web UI（HTML + 静态资源 + /api 与 /event），
所以不需要单独跑 `opencode web`——把 4096 反代出去即可。

走 :8443 的新前缀 /opencode/，和现有的 /kimi-apk、/opencode-relay 并列。
admin API 的改动不落盘，Caddy 重启即失，所以要配看门狗（keepalive.sh 会重灌）。
"""
import argparse
import json
import os
import sys
import urllib.request

ADMIN = os.environ.get("CADDY_ADMIN", "http://127.0.0.1:2019")
SERVER = os.environ.get("CADDY_SERVER", "srv2")   # srv2 = :8443
ROUTE_PATH = os.environ.get("WEB_ROUTE_PATH", "/opencode")
UPSTREAM = os.environ.get("WEB_UPSTREAM", "127.0.0.1:4096")
HOST = os.environ.get("WEB_PUBLIC_HOST", "43.143.76.55")


def admin_get(path):
    with urllib.request.urlopen(ADMIN + path, timeout=10) as resp:
        return json.loads(resp.read())


def admin_post_config(cfg):
    req = urllib.request.Request(
        ADMIN + "/config/", data=json.dumps(cfg).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def build_route():
    return {
        "match": [{"host": [HOST], "path": [ROUTE_PATH, ROUTE_PATH + "/*"]}],
        "handle": [{
            "handler": "subroute",
            "routes": [
                {"handle": [{"handler": "rewrite", "strip_path_prefix": ROUTE_PATH}]},
                {"handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": UPSTREAM}],
                    # WebSocket（/event SSE、终端）要 h1；h2 连不上
                    "transport": {"protocol": "http", "versions": ["1.1"]},
                }]},
            ],
        }],
        "terminal": True,
    }


def is_ours(route):
    """只认本脚本注入的这条（前缀 + 上游都要对得上）。

    /opencode-relay 的路径里也含 /opencode 字样，所以不能只做子串判断 ——
    必须看它 match 的 path 里是否恰好有本脚本的前缀。
    """
    for m in route.get("match") or []:
        paths = m.get("path") or []
        if ROUTE_PATH in paths and (ROUTE_PATH + "/*") in paths:
            return True
    return False


def route_is_current(routes):
    ours = [r for r in routes if is_ours(r)]
    if len(ours) != 1:
        return False
    return is_ours(routes[0]) and UPSTREAM in json.dumps(routes[0])


def install():
    cfg = admin_get("/config/")
    servers = cfg["apps"]["http"]["servers"]
    if SERVER not in servers:
        print(f"  ! Caddy 里没有 {SERVER}")
        return False
    routes = servers[SERVER].get("routes", [])
    if route_is_current(routes):
        print("  ✓ Web 路由已就位")
        return True
    others = [r for r in routes if not is_ours(r)]
    servers[SERVER]["routes"] = [build_route()] + others
    admin_post_config(cfg)
    print("  ✓ 已注入 Web 路由")
    return True


def check():
    try:
        cfg = admin_get("/config/")
        routes = cfg["apps"]["http"]["servers"][SERVER].get("routes", [])
        if route_is_current(routes):
            print("  ✓ Web 路由已就位")
            return True
        print("  ! Web 路由缺失或重复")
        return False
    except Exception as exc:
        print(f"  ! 读取 Caddy 配置失败: {exc}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        sys.exit(0 if check() else 1)
    try:
        install()
    except Exception as exc:
        print(f"  ! 失败: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
