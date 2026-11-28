"""Alert delivery. Secrets stay in environment variables.

Nothing here invents a successful send. Missing SMTP or Telegram settings
return sent=false. The free Render web process is not a 24/7 worker.
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from workbench.crosses import ARCHIVED_ALERTS, ACTIVE_ALERTS
from workbench.engine_path import PACKAGE

CHANNELS = ("in_app", "browser", "email", "telegram_channel", "telegram_dm", "sound")
CHANNEL_COPY = {
    "in_app": {"label": "页面里", "detail": "只在这个页面打开时出现"},
    "browser": {"label": "浏览器通知", "detail": "需要允许通知，关掉页面后到不了"},
    "email": {"label": "邮箱", "detail": "服务器配好邮箱后才能发出"},
    "telegram_channel": {"label": "Telegram 频道", "detail": "服务器配好机器人后才能发到频道"},
    "telegram_dm": {"label": "Telegram 私聊", "detail": "服务器配好机器人后才能发到私聊"},
    "sound": {"label": "提示音", "detail": "只在这个页面打开时响"},
}


def _present(name):
    return bool((os.environ.get(name) or "").strip())


def prefs_path():
    override = os.environ.get("QSL_ALERT_PREFS")
    return Path(override) if override else PACKAGE.parent / "workbench" / "data" / "alert_channels.json"


def load_prefs():
    enabled = {name: name == "in_app" for name in CHANNELS}
    path = prefs_path()
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        for name in CHANNELS:
            if name in stored:
                enabled[name] = bool(stored[name])
    enabled["position_changes"] = False
    return enabled


def set_channel(channel, enabled):
    if channel not in CHANNELS:
        raise ValueError("未知渠道")
    current = load_prefs()
    current.pop("position_changes", None)
    current[channel] = bool(enabled)
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def enabled_channels():
    prefs = load_prefs()
    return [name for name in CHANNELS if prefs.get(name)]


def channel_config():
    email_ready = _present("QSL_SMTP_HOST") and _present("QSL_SMTP_FROM") and _present("QSL_ALERT_EMAIL_TO")
    token = _present("QSL_TELEGRAM_BOT_TOKEN")
    return {
        "in_app": {"configured": True, "enabled_default": True},
        "browser": {"configured": True, "enabled_default": False, "note": "需要浏览器授权，关页后不到达"},
        "email": {
            "configured": email_ready,
            "enabled_default": False,
            "env": ["QSL_SMTP_HOST", "QSL_SMTP_PORT", "QSL_SMTP_USER", "QSL_SMTP_PASSWORD", "QSL_SMTP_FROM", "QSL_ALERT_EMAIL_TO"],
        },
        "telegram_channel": {
            "configured": token and _present("QSL_TELEGRAM_CHANNEL_ID"),
            "enabled_default": False,
            "env": ["QSL_TELEGRAM_BOT_TOKEN", "QSL_TELEGRAM_CHANNEL_ID"],
        },
        "telegram_dm": {
            "configured": token and _present("QSL_TELEGRAM_DM_CHAT_ID"),
            "enabled_default": False,
            "env": ["QSL_TELEGRAM_BOT_TOKEN", "QSL_TELEGRAM_DM_CHAT_ID"],
        },
        "sound": {"configured": True, "enabled_default": False, "note": "只在页面打开时响"},
        "active_alerts": list(ACTIVE_ALERTS),
        "archived_alerts": list(ARCHIVED_ALERTS),
        "trader_events_default": False,
        "enabled": load_prefs(),
        "copy": CHANNEL_COPY,
        "position_changes": "持仓变化是单独一类，默认关闭，不会和六种指标交叉混在一起推送。",
    }


def runtime_status():
    return {
        "web_process_is_24h": False,
        "render_free_sleeps": True,
        "reason": "Render 免费 Web 空闲会休眠，关页后这个网站进程不能保证继续监控。",
        "user_note": "关掉页面后，这个免费网站不能保证继续提醒。邮箱和 Telegram 要等服务器配好，并且有一台不休眠的检查程序，才会在关页后发出。",
        "always_on_command": "python -m workbench.monitor_worker",
        "always_on_note": "把这条命令跑在不休眠的 Worker 或常驻机器上，才会在关页后继续检查并发邮箱或 Telegram。免费 Web 进程本身不是 24 小时服务。",
    }


def format_message(event):
    lines = [
        "【Signal Lab｜指标交叉提醒】",
        f"交易品种：{event.get('symbol')}",
        "交易平台：Bitget",
        f"K线周期：{event.get('interval')}",
        f"交叉类型：{event.get('label')}",
        f"当前价格：{event.get('price')}",
        f"EMA{event.get('ema')}：{event.get('ema_value')}",
        f"布林中轨：{event.get('boll_mid')}",
        f"触发时间：{event.get('time_text') or event.get('time')}",
        "确认状态：K线收盘确认",
        event.get("note") or "通知只描述指标事件，不是开仓指令。",
    ]
    return "\n".join(lines)


def _smtp_send(text):
    host = os.environ["QSL_SMTP_HOST"].strip()
    port = int(os.environ.get("QSL_SMTP_PORT") or "587")
    sender = os.environ["QSL_SMTP_FROM"].strip()
    recipient = os.environ["QSL_ALERT_EMAIL_TO"].strip()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Signal Lab｜指标交叉提醒"
    message.set_content(text)
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        user = (os.environ.get("QSL_SMTP_USER") or "").strip()
        password = os.environ.get("QSL_SMTP_PASSWORD") or ""
        if user:
            smtp.login(user, password)
        smtp.send_message(message)
    return recipient


def _telegram_send(text, chat_id):
    token = os.environ["QSL_TELEGRAM_BOT_TOKEN"].strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram 拒绝")
    return str(chat_id)


def deliver(channel, text):
    """Try one channel. Never reports success when nothing was sent."""
    config = channel_config()
    if channel in {"in_app", "browser", "sound"}:
        return {"ok": True, "sent": channel == "in_app", "channel": channel, "reason": "由打开的页面展示或发声，服务端不代替浏览器。"}
    spec = config.get(channel) or {}
    if not load_prefs().get(channel):
        return {"ok": True, "sent": False, "channel": channel, "reason": "这个渠道已关闭，没有发送。"}
    if not spec.get("configured"):
        return {"ok": True, "sent": False, "channel": channel, "reason": "未配置所需环境变量，没有发送。"}
    try:
        if channel == "email":
            target = _smtp_send(text)
        elif channel == "telegram_channel":
            target = _telegram_send(text, os.environ["QSL_TELEGRAM_CHANNEL_ID"].strip())
        elif channel == "telegram_dm":
            target = _telegram_send(text, os.environ["QSL_TELEGRAM_DM_CHAT_ID"].strip())
        else:
            return {"ok": False, "sent": False, "channel": channel, "reason": "未知渠道"}
    except Exception as exc:
        return {"ok": True, "sent": False, "channel": channel, "reason": f"{type(exc).__name__}: 发送失败"}
    return {"ok": True, "sent": True, "channel": channel, "target": target}


def deliver_event(event, channels):
    text = format_message(event)
    results = []
    for channel in channels:
        results.append(deliver(channel, text))
    return {"text": text, "results": results}
