#!/usr/bin/env python3
"""把 opencode 的 Web UI 暴露到 https://43.143.76.55:8443/opencode/

难点
----
opencode 的 Web UI 是 SPA，HTML 里写死**绝对路径**资源：
    <script src="/assets/index-xxx.js">
挂在 /opencode/ 下时浏览器会去站点根请求 /assets/...，被同端口的其它站点吞掉
（返回一段 HTML），JS 加载失败 → 白屏。

两种常见修法在这里都不可行：
  * 注入 <base href>（改响应体）—— 需要 Caddy 的 replace handler，
    官方精简版 Caddy 没带这个模块（unknown module: http.handlers.replace）；
  * 独立端口（:8444）—— 云安全组没放行，连接超时。

所以用**兜底路由**：在 8443 站点上，除了 /opencode/* 前缀，
再额外把 SPA 会去请求的那几个根路径（/assets/*、/favicon*、/site.webmanifest、
/api/*、/event …）也反代到 4096。这样浏览器按绝对路径请求也照样能拿到资源。

风险：这些根路径本该属于 8443 上的主应用。实测主应用不提供 /assets/ 下的
文件名（返回的是它的 index.html），所以让 opencode 接管这些具体路径不会抢走
真实资源；但 /api/* 这种通用前缀**不要**接管，只接管 SPA 实际用到的那几个。

由 keepalive.sh 的看门狗重灌（admin API 改动不落盘）。
"""
import argparse
import json
import os
import sys
import urllib.request

ADMIN = os.environ.get("CADDY_ADMIN", "http://127.0.0.1:2019")
SERVER = os.environ.get("CADDY_SERVER", "srv2")     # srv2 = :8443
ROUTE_PATH = os.environ.get("WEB_ROUTE_PATH", "/opencode")
UPSTREAM = os.environ.get("WEB_UPSTREAM", "127.0.0.1:4096")
HOST = os.environ.get("WEB_PUBLIC_HOST", "43.143.76.55")

# SPA 会按绝对路径请求的根路径。只列确定属于 opencode 的，
# 不用 /api/* 这类通用前缀（会和 8443 上的主应用抢）。
ASSET_PATHS = [
    "/assets/*",
    "/favicon*",
    "/apple-touch-icon*",
    "/site.webmanifest",
    "/social-share.png",
    "/opencode*",
]


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


def _proxy():
    return {
        "handler": "reverse_proxy",
        "upstreams": [{"dial": UPSTREAM}],
        # /event 是 SSE，必须 h1
        "transport": {"protocol": "http", "versions": ["1.1"]},
    }


def build_route():
    return {
        "match": [{"host": [HOST], "path": [ROUTE_PATH, ROUTE_PATH + "/*"]}],
        "handle": [{
            "handler": "subroute",
            "routes": [
                {"handle": [{"handler": "rewrite", "strip_path_prefix": ROUTE_PATH}]},
                {"handle": [_proxy()]},
            ],
        }],
        "terminal": True,
    }


def build_asset_route():
    return {
        "match": [{"host": [HOST], "path": ASSET_PATHS}],
        "handle": [{"handler": "subroute", "routes": [{"handle": [_proxy()]}]}],
        "terminal": True,
    }


def is_web_route(route):
    for m in route.get("match") or []:
        ps = m.get("path") or []
        if ROUTE_PATH in ps and (ROUTE_PATH + "/*") in ps:
            return True
    return False


def is_asset_route(route):
    for m in route.get("match") or []:
        ps = m.get("path") or []
        if "/assets/*" in ps:
            return True
    return False


def route_is_current(routes):
    web = [r for r in routes if is_web_route(r)]
    ast = [r for r in routes if is_asset_route(r)]
    if len(web) != 1 or len(ast) != 1:
        return False
    # 两条都必须排在 catch-all 之前
    return is_web_route(routes[0]) and is_asset_route(routes[1])


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
    others = [r for r in routes if not is_web_route(r) and not is_asset_route(r)]
    servers[SERVER]["routes"] = [build_route(), build_asset_route()] + others
    admin_post_config(cfg)
    print("  ✓ 已注入 Web 路由（/opencode + SPA 资源路径）")
    return True


def check():
    try:
        cfg = admin_get("/config/")
        if route_is_current(cfg["apps"]["http"]["servers"][SERVER].get("routes", [])):
            print("  ✓ Web 路由已就位")
            return True
        print("  ! Web 路由缺失或不匹配")
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
