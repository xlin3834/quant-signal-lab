"""Small JSON client with retries. It does not sign orders or store secrets."""
import time

import requests

from evidence import SourceError


def request_json(method, url, *, params=None, body=None, headers=None, attempts=3,
                 timeout=(3, 8), transport=None, sleeper=time.sleep):
    transport = transport or requests.request
    last_status = None
    last_error = "连接失败"
    for attempt in range(attempts):
        try:
            response = transport(method, url, params=params, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            if attempt + 1 < attempts:
                sleeper(0.2 * (attempt + 1))
            continue
        last_status = getattr(response, "status_code", None)
        if last_status == 200:
            try:
                return response.json(), last_status
            except ValueError as exc:
                raise SourceError("响应不是 JSON", last_status) from exc
        if last_status in (408, 429) or (isinstance(last_status, int) and 500 <= last_status < 600):
            if attempt + 1 < attempts:
                sleeper(0.2 * (attempt + 1))
            continue
        detail = _detail(response)
        raise SourceError(f"HTTP {last_status} {detail}".strip(), last_status)
    raise SourceError(f"重试后仍失败（{last_error if last_status is None else last_status}）", last_status)


def _detail(response):
    try:
        payload = response.json()
    except ValueError:
        return str(getattr(response, "text", ""))[:180]
    if isinstance(payload, dict):
        return str(payload.get("msg") or payload.get("message") or payload.get("code") or "")[:180]
    return ""
