"""Write protection and a small in-process rate limit.

No login service and no new dependency. If QSL_ADMIN_TOKEN is unset, only a
loopback client may change shared settings. If it is set, every write must
send that token. The peer address is request.client.host; X-Forwarded-For is
not trusted, so a public proxy cannot pretend to be localhost.
"""
from __future__ import annotations

import hmac
import os
import time
from threading import Lock

WRITE_PREFIXES = ("/api/settings", "/api/presets")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class WindowLimiter:
    def __init__(self):
        self.events = {}
        self.lock = Lock()

    def allow(self, key, limit, window=60.0):
        now = time.monotonic()
        with self.lock:
            bucket = [stamp for stamp in self.events.get(key, ()) if now - stamp < window]
            if len(bucket) >= limit:
                self.events[key] = bucket
                return False
            bucket.append(now)
            self.events[key] = bucket
            return True


def admin_token():
    return os.environ.get("QSL_ADMIN_TOKEN", "").strip()


def write_limit():
    try:
        return max(1, int(os.environ.get("QSL_WRITE_LIMIT", "30")))
    except ValueError:
        return 30


def client_host(request):
    client = request.client
    return "" if client is None or not client.host else client.host


def is_loopback(host):
    return host in {"127.0.0.1", "::1", "localhost"}


def token_matches(provided, expected):
    if not provided or not expected:
        return False
    left = provided.encode()
    right = expected.encode()
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def provided_token(request):
    header = request.headers.get("x-admin-token", "")
    if header:
        return header
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def write_decision(request):
    """Return (allowed, message). Does not disclose the token."""
    expected = admin_token()
    if expected:
        if token_matches(provided_token(request), expected):
            return True, "已使用管理令牌。"
        return False, "写入需要请求头 X-Admin-Token。公网和本机在设置了 QSL_ADMIN_TOKEN 后都要带这个令牌。"
    if is_loopback(client_host(request)):
        return True, "未设置管理令牌，仅本机回环地址可以写入。"
    return False, "未设置 QSL_ADMIN_TOKEN。公网来源不能修改全站设置；只有本机 127.0.0.1 可以写入。"


def write_policy():
    if admin_token():
        return {
            "mode": "token",
            "remote_writes": False,
            "note": "已配置管理令牌。保存设置、同步参数和预设写入都要在请求里带 X-Admin-Token。令牌只留在服务端环境变量和你的浏览器会话里，不会写进设置文件。",
        }
    return {
        "mode": "loopback",
        "remote_writes": False,
        "note": "未配置 QSL_ADMIN_TOKEN。公网访客不能覆盖全站设置；只有从本机 127.0.0.1 打开的页面可以写入。要在公开地址上保存，请在服务端设置该环境变量。",
    }


def settings_write(path, method):
    return method.upper() in WRITE_METHODS and any(path.startswith(prefix) for prefix in WRITE_PREFIXES)


def mutation(path, method):
    return path.startswith("/api/") and method.upper() in WRITE_METHODS
