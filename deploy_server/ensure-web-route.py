#!/usr/bin/env python3
"""让 opencode 的 Web UI 在 https://43.143.76.55:8443/opencode/ 下完整可用。

问题
----
opencode 的 Web UI 是 SPA，前端把 API 和静态资源地址**硬编码成绝对路径**：

    fetch("/global/health")      <script src="/assets/index-xxx.js">

挂在 /opencode/ 下时这些请求会打到站点根，被 8443 上的主应用（HAPI/DSH）
用它的 SPA fallback 接走 —— 返回一段 HTML。表现就是「页面能打开，但没有模型、
没法对话」。

为什么可以直接接管这些根路径
--------------------------
实测主应用对这些路径返回的是**同一个 SPA fallback 页面**（md5 相同），
说明它根本不提供这些 API，只是 catch-all 兜底。所以把 opencode 用到的
根路径转给 4096 不会影响主应用任何功能。

真正属于主应用的（/、/api/v1/*、静态资源等）**不动**。

由 keepalive.sh 的看门狗重灌（admin API 改动不落盘）。
"""
import argparse
import json
import os
import sys
import urllib.request

ADMIN = os.environ.get("CADDY_ADMIN", "http://127.0.0.1:2019")
SERVER = os.environ.get("CADDY_SERVER", "srv2")     # srv2 = :8443
PREFIX = os.environ.get("WEB_ROUTE_PATH", "/opencode")
UPSTREAM = os.environ.get("WEB_UPSTREAM", "127.0.0.1:4096")
HOST = os.environ.get("WEB_PUBLIC_HOST", "43.143.76.55")

# opencode SPA 会用到的根路径。只列它真正需要的；主应用自己也在用的
# （/、/api/v1/*）绝不碰。
OC_PATHS = [
    "/global/*",          # /global/health、/global/event(SSE)
    "/config/*",          # /config/providers
    "/config",            # 配置本体
    "/provider", "/provider/*",
    "/agent", "/agent/*",
    "/session", "/session/*",
    "/project", "/project/*",
    "/app", "/app/*",
    "/event",             # 旧版 SSE 路径
    "/file", "/file/*",
    "/find", "/find/*",
    "/assets/*",          # 前端 JS/CSS
    "/favicon*",
    "/apple-touch-icon*",
    "/site.webmanifest",
    "/social-share.png",
]


def admin_get(path):
    with urllib.request.urlopen(ADMIN + path, timeout=10) as resp:
        return json.loads(resp.read())


def admin_post_config(cfg):
    req = urllib.request.Request(
        ADMIN + "/config/", data=json.dumps(cfg).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def _proxy():
    return {
        "handler": "reverse_proxy",
        "upstreams": [{"dial": UPSTREAM}],
        # /event 是 SSE，终端走 WebSocket，必须 h1
        "transport": {"protocol": "http", "versions": ["1.1"]},
    }


def build_prefix_route():
    """带前缀的入口：/opencode/* → 剥前缀后转给 opencode。"""
    return {
        "match": [{"host": [HOST], "path": [PREFIX, PREFIX + "/*"]}],
        "handle": [{
            "handler": "subroute",
            "routes": [
                {"handle": [{"handler": "rewrite", "strip_path_prefix": PREFIX}]},
                {"handle": [_proxy()]},
            ],
        }],
        "terminal": True,
    }


def build_mcp_stub_route():
    """给 opencode 1.18.34 缺失的 /api/mcp/* 返回空 JSON。

    这一版的前端会 `GET /api/mcp/resource`（新版有、1.18.34 没有），请求落到
    SPA 的 fallback 上拿到一段 HTML；前端把它当 JSON 用，
    `Object.values(html).map(B => B.name.length)` 就抛
    `TypeError: can't access property "length", B.name is undefined`，
    整个界面白屏。

    返回 `{}` 让 `Object.values({})` 得到空数组，前端就正常渲染了。
    """
    return {
        "match": [{"host": [HOST], "path": ["/api/mcp", "/api/mcp/*"]}],
        "handle": [{
            "handler": "static_response",
            "headers": {"Content-Type": ["application/json"]},
            "body": "{}",
        }],
        "terminal": True,
    }


def build_api_route():
    """SPA 按绝对路径请求的那些根路径。"""
    return {
        "match": [{"host": [HOST], "path": OC_PATHS}],
        "handle": [{"handler": "subroute", "routes": [{"handle": [_proxy()]}]}],
        "terminal": True,
    }


def _is_prefix_route(r):
    for m in r.get("match") or []:
        ps = m.get("path") or []
        if PREFIX in ps and (PREFIX + "/*") in ps:
            return True
    return False


def _is_api_route(r):
    for m in r.get("match") or []:
        ps = m.get("path") or []
        if "/global/*" in ps:
            return True
    return False


def _is_stub(r):
    for m in r.get("match") or []:
        if "/api/mcp" in (m.get("path") or []):
            return True
    return False


def _is_ours(r):
    return _is_prefix_route(r) or _is_api_route(r) or _is_stub(r)


def _catch_all_index(routes):
    """第一条「没有 path 匹配」的路由下标（catch-all）；没有则返回末尾。"""
    for i, r in enumerate(routes):
        if not any(m.get("path") for m in (r.get("match") or [])):
            return i
    return len(routes)


def route_is_current(routes):
    """两条路由都在，且都排在 catch-all 之前。"""
    web = [r for r in routes if _is_prefix_route(r)]
    api = [r for r in routes if _is_api_route(r)]
    if len(web) != 1 or len(api) != 1:
        return False
    if "/api/v1" in json.dumps(api[0]):
        return False
    # 它们的位置必须都在 catch-all 之前，否则会被同端口的其它路由吃掉
    idx_catch = _catch_all_index(routes)
    pos_web = routes.index(web[0])
    pos_api = routes.index(api[0])
    return pos_web < idx_catch and pos_api < idx_catch


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
    # 插在第 0 位会踩别人：同端口还有 kimi-apk 的路由（每 3 分钟由它自己的
    # 看门狗重灌）。只保证排在**真正的 catch-all** 之前，不打乱其它人的顺序。
    others = [r for r in routes if not _is_ours(r) and not _is_stub(r)]
    idx = _catch_all_index(others)
    others.insert(idx, build_api_route())
    others.insert(idx, build_prefix_route())
    # 桩路由要排在这两条之前（更具体的路径优先）
    others.insert(idx, build_mcp_stub_route())
    servers[SERVER]["routes"] = others
    admin_post_config(cfg)
    print(f"  ✓ 已注入 Web 路由（{PREFIX} + {len(OC_PATHS)} 条 SPA 根路径）")
    return True


def check():
    try:
        cfg = admin_get("/config/")
        routes = cfg["apps"]["http"]["servers"][SERVER].get("routes", [])
        if route_is_current(routes):
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
