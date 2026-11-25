"""Public GitHub metadata and a local, non-executing score.

The score is not a GitHub security certification. Repository code is not cloned
or imported.
"""
import os
from datetime import datetime, timezone

from evidence import SourceError
from research_http import request_json

API = "https://api.github.com"
DOCS = "https://docs.github.com/en/rest/repos/repos"
WATCH = (
    "ccxt/ccxt",
    "bmoscon/cryptofeed",
    "freqtrade/freqtrade",
    "polakowo/vectorbt",
    "ta-lib/ta-lib",
    "tradingview/lightweight-charts",
    "nautechsystems/nautilus_trader",
    "microsoft/qlib",
    "hyperliquid-dex/hyperliquid-python-sdk",
)
PERMISSIVE = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "0BSD"}
COPYLEFT = {"GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later"}
WEAK = {"LGPL-3.0", "LGPL-3.0-only", "LGPL-3.0-or-later", "MPL-2.0"}


def fetch_repo(full_name, *, transport=None, token=None):
    owner, _, name = full_name.partition("/")
    if not owner or not name or "/" in name:
        raise SourceError("仓库名需要 owner/name")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "QuantSignalLab-research"}
    secret = token if token is not None else os.environ.get("GITHUB_TOKEN")
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    payload, status = request_json("GET", f"{API}/repos/{owner}/{name}", headers=headers, transport=transport)
    if not isinstance(payload, dict) or "full_name" not in payload:
        raise SourceError("GitHub 仓库响应异常", status)
    return normalize_repo(payload), secret is not None


def normalize_repo(payload):
    license_info = payload.get("license") or {}
    return {
        "full_name": payload.get("full_name"),
        "html_url": payload.get("html_url"),
        "description": payload.get("description") or "",
        "license_spdx": license_info.get("spdx_id") or "NOASSERTION",
        "pushed_at": payload.get("pushed_at"),
        "archived": bool(payload.get("archived")),
        "stars": int(payload.get("stargazers_count") or 0),
        "language": payload.get("language") or "",
        "open_issues": int(payload.get("open_issues_count") or 0),
        "docs": DOCS,
    }


def score_repo(repo, now=None):
    now = now or datetime.now(timezone.utc)
    license_score, license_note = _license(repo.get("license_spdx"))
    days = _age_days(repo.get("pushed_at"), now)
    if repo.get("archived"):
        maintenance, maintenance_note = 0, "仓库已归档"
    elif days is None:
        maintenance, maintenance_note = 10, "没有 pushed_at"
    elif days <= 31:
        maintenance, maintenance_note = 90, f"最近推送距今 {days} 天"
    elif days <= 180:
        maintenance, maintenance_note = 60, f"最近推送距今 {days} 天"
    else:
        maintenance, maintenance_note = 30, f"最近推送距今 {days} 天"
    stars = repo.get("stars") or 0
    quality = round(30 + min(stars, 20000) / 20000 * 50 + (20 if repo.get("description") else 0))
    compatible = repo.get("language") in {"Python", "TypeScript", "JavaScript", "Rust", "C", "C++", "Go"}
    return {
        "license_score": license_score,
        "license_note": license_note,
        "maintenance_score": maintenance,
        "maintenance_note": maintenance_note,
        "quality_score": quality,
        "quality_note": "只用星标和简介作粗略排序，不是代码质量认证。",
        "security_score": None,
        "security_note": "未克隆、未运行、未做依赖漏洞扫描。",
        "compatibility_score": 70 if compatible else 40,
        "compatibility_note": "语言接近本仓库技术栈时给 70，否则 40。禁止整库覆盖本系统。",
        "adopt": license_score >= 80 and not repo.get("archived"),
    }


def _license(spdx):
    if spdx in PERMISSIVE:
        return 90, "宽松许可证。可以研究接口，仍须保留版权声明，不能整库搬进来。"
    if spdx in WEAK:
        return 40, "弱 copyleft。不把源码复制进本系统；若将来单独进程调用，先做许可审查。"
    if spdx in COPYLEFT:
        return 20, "copyleft。禁止并入本系统。"
    return 30, "SPDX 不能确认许可证。需要人工阅读 LICENSE，不能当作可合并。"


def _age_days(value, now):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (now - stamp).days)
