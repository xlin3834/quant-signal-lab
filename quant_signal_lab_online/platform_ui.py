"""Research platform pages. Missing sources stay visibly unavailable."""
import os
import subprocess
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import research_store as store
from bitget_structure import COPY_REQUIREMENT, last_price, load_structure
from evidence import GRADES, bitget_symbol_for, crosscheck, screenshot_evidence, social_evidence
from freshness import can_call_current, classify, now_ms, status_sentence
from github_public import WATCH, fetch_repo, score_repo
from hyperliquid_public import extract_address, fetch_account, position_changes
from market_data import MarketDataError
from okx_public import fetch_positions, fetch_ranks, parse_data_ver
from platform_status import MODULES, VENUES
from research_http import request_json


def platform_page():
    conn = store.connect(store.default_path())
    st.subheader("研究平台")
    st.caption("只读公开数据和研究记录。不下单，不自动发布，不把过期数据标成实时。")
    section = st.selectbox("板块", (
        "模块与来源", "交易员监控", "市场结构", "开源项目", "研究资料", "规则说明", "任务与版本"))
    try:
        if section == "模块与来源":
            _matrix()
        elif section == "交易员监控":
            _traders(conn)
        elif section == "市场结构":
            _structure(conn)
        elif section == "开源项目":
            _github(conn)
        elif section == "研究资料":
            _library(conn)
        elif section == "规则说明":
            _assistant()
        else:
            _tasks(conn)
    finally:
        conn.close()


def _matrix():
    st.dataframe(pd.DataFrame(MODULES)[["name", "state", "reason"]], hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame(VENUES)[["name", "state", "grade", "reason"]], hide_index=True, width="stretch")
    for grade, text in GRADES.items():
        st.caption(f"{grade} · {text}")
    st.caption(social_evidence()["label"])
    st.caption(screenshot_evidence()["label"])


def _traders(conn):
    venue = st.radio("来源", ("Hyperliquid", "OKX", "暂不可用"), horizontal=True)
    if venue == "Hyperliquid":
        _hyperliquid(conn)
    elif venue == "OKX":
        _okx(conn)
    else:
        _blocked(conn)


def _hyperliquid(conn):
    st.caption("证据等级 B。官方 info 按地址返回公开账户状态，不是 A 类授权只读 Key。")
    st.caption("文档 https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint")
    text = st.text_input("地址，或含有一个 0x 地址的公开链接")
    threshold = st.number_input("未实现盈亏提醒阈值（USDT，0 为不提醒）", min_value=0.0, value=50.0, step=10.0)
    if st.button("读取 Hyperliquid 官方账户", type="primary"):
        try:
            address = extract_address(text)
            previous = _previous_positions(conn, f"hyperliquid:{address}")
            account = fetch_account(address, received_ms=now_ms())
            status, latency = classify(account["exchange_time_ms"], account["received_time_ms"], now_ms(), 60_000)
            store.add_snapshot(
                conn, venue="hyperliquid", entity_key=f"hyperliquid:{address}", grade="B",
                exchange_time_ms=account["exchange_time_ms"], received_time_ms=account["received_time_ms"],
                connection_status=status, payload=account,
            )
            for message in position_changes(previous, account["positions"], threshold):
                store.add_alert(conn, f"hyperliquid:{address}", "B", message)
            st.session_state.hl_view = account | {"status": status, "latency": latency}
        except Exception as exc:
            st.error(f"Hyperliquid 不可用：{exc}")
    view = st.session_state.get("hl_view")
    if not view:
        st.info("还没有读取。不会展示示例交易员或示例盈亏。")
        return
    _freshness(view["status"], view.get("latency"), view.get("exchange_time_ms"), view.get("received_time_ms"))
    _favorite_button(conn, "trader", f"hyperliquid:{view['address']}", view["address"])
    win = view["win_rate"]
    rate = "缺失" if win["rate"] is None else f"{win['rate'] * 100:.1f}%（{win['samples']} 条）"
    st.write(f"账户价值 { _money(view.get('account_value')) } · 按 fills 计算的胜率 {rate}")
    st.caption(win["note"])
    drawdown = view["max_drawdown"]
    if drawdown.get("value") is None:
        st.caption(drawdown["note"])
    else:
        st.caption(f"最大回撤 {drawdown['value'] * 100:.2f}% · 窗口 {drawdown['window']} · {drawdown['note']}")
    _positions(conn, view["positions"], view["status"])
    if view["equity_curve"]:
        curve = pd.DataFrame(view["equity_curve"])
        curve["time"] = pd.to_datetime(curve.time_ms, unit="ms", utc=True)
        st.line_chart(curve.set_index("time")["value"])
        st.caption("曲线来自 portfolio 的 accountValueHistory。")
    _alerts(conn)


def _okx(conn):
    st.caption("证据等级 B。公开带单不是账户主人的只读 Key，也没有在榜单字段里看到最大回撤。")
    st.caption("文档 https://www.okx.com/docs-v5/en/")
    page = st.number_input("公开榜页码", min_value=1, max_value=50, value=1, step=1)
    query = st.text_input("在已读取的榜单里搜索昵称或 uniqueCode")
    if st.button("读取 OKX 公开榜", type="primary"):
        try:
            ranks = fetch_ranks(page=int(page))
            received = now_ms()
            exchange = parse_data_ver(ranks.get("data_ver"))
            status, latency = classify(exchange, received, now_ms(), 15 * 60_000)
            store.add_snapshot(
                conn, venue="okx", entity_key=f"okx:ranks:{page}", grade="B",
                exchange_time_ms=exchange, received_time_ms=received, connection_status=status, payload=ranks,
            )
            st.session_state.okx_ranks = ranks | {"status": status, "latency": latency,
                                                  "exchange_time_ms": exchange, "received_time_ms": received}
        except Exception as exc:
            st.error(f"OKX 公开榜不可用：{exc}")
    ranks = st.session_state.get("okx_ranks")
    if ranks:
        _freshness(ranks["status"], ranks.get("latency"), ranks.get("exchange_time_ms"), ranks.get("received_time_ms"))
        rows = [row for row in ranks["ranks"] if query.strip().lower() in
                f"{row['nickname']} {row['unique_code']}".lower()]
        if not rows:
            st.info("这一页没有匹配。公开榜每页最多 20 条，搜索不会自动翻完整张榜。")
        else:
            st.dataframe(pd.DataFrame([{
                "昵称": row["nickname"], "uniqueCode": row["unique_code"], "证据": "B",
                "胜率": None if row["win_ratio"] is None else row["win_ratio"] * 100,
                "90日盈亏": row["pnl"], "最大回撤": "接口未提供",
            } for row in rows]), hide_index=True, width="stretch")
            st.caption(rows[0]["max_drawdown_note"])
            selected = st.selectbox("查看公开带单", [row["unique_code"] for row in rows])
            _favorite_button(conn, "trader", f"okx:{selected}", selected)
            if st.button("读取该 uniqueCode 的公开带单"):
                _load_okx_positions(conn, selected)
    else:
        st.info("还没有读取公开榜。")
    code = st.text_input("或直接输入 uniqueCode")
    if st.button("按 uniqueCode 读取"):
        _load_okx_positions(conn, code)
    detail = st.session_state.get("okx_detail")
    if detail:
        _freshness(detail["status"], None, None, detail["received_time_ms"])
        win = detail["win_rate"]
        rate = "缺失" if win["rate"] is None else f"{win['rate'] * 100:.1f}%（{win['samples']} 条）"
        st.write(f"历史公开带单胜率 {rate}")
        st.caption(win["note"] + " " + detail["max_drawdown_note"])
        _positions(conn, detail["positions"], detail["status"])
    _alerts(conn)


def _load_okx_positions(conn, code):
    try:
        previous = _previous_positions(conn, f"okx:{code.strip()}")
        detail = fetch_positions(code)
        received = now_ms()
        status, _ = classify(None, received, now_ms(), 60_000)
        store.add_snapshot(
            conn, venue="okx", entity_key=f"okx:{detail['unique_code']}", grade="B",
            exchange_time_ms=None, received_time_ms=received, connection_status=status, payload=detail,
        )
        for message in position_changes(previous, detail["positions"], 50):
            store.add_alert(conn, f"okx:{detail['unique_code']}", "B", message)
        st.session_state.okx_detail = detail | {"status": status, "received_time_ms": received}
    except Exception as exc:
        st.error(f"OKX 公开带单不可用：{exc}")


def _blocked(conn):
    st.warning("下面的来源在本版本不能读取交易员。页面不填充示例持仓。")
    for item in VENUES:
        if item["state"] != "阻塞":
            continue
        st.markdown(f"**{item['name']} · {item['state']}**")
        st.caption(item["reason"])
    if st.button("检测 Binance 连通性"):
        status, detail = _probe_binance()
        store.save_probe(conn, "binance", status, detail)
    probe = store.latest_probe(conn, "binance")
    if probe:
        st.caption(f"上次检测 {probe['checked_at']} · {probe['status']} · {probe['detail']}")
    st.caption(COPY_REQUIREMENT)


def _structure(conn):
    st.caption("Bitget 官方公开市场。资金费、持仓量和成交不属于某个交易员。")
    symbol = st.text_input("USDT 永续合约", value="BTCUSDT")
    if st.button("读取市场结构", type="primary"):
        try:
            payload = load_structure(symbol)
            received = now_ms()
            exchange = payload["ticker"]["exchange_time_ms"]
            status, latency = classify(exchange, received, now_ms(), 90_000)
            store.add_snapshot(
                conn, venue="bitget", entity_key=f"structure:{payload['symbol']}", grade=None,
                exchange_time_ms=exchange, received_time_ms=received, connection_status=status, payload=payload,
            )
            st.session_state.structure = payload | {"status": status, "latency": latency, "received_time_ms": received}
        except (MarketDataError, ValueError) as exc:
            st.error(str(exc))
    view = st.session_state.get("structure")
    if not view:
        st.info("还没有读取。")
        return
    _freshness(view["status"], view.get("latency"), view["ticker"]["exchange_time_ms"], view["received_time_ms"])
    ticker = view["ticker"]
    st.write(
        f"{view['symbol']} · 最新价 {_money(ticker['price'])} · 标记价 {_money(ticker['mark'])} · "
        f"指数价 {_money(ticker['index'])}"
    )
    st.write(
        f"资金费率 {_money(ticker['funding_rate'])} · 持仓量 {_money(ticker['open_interest'])} · "
        f"24h 成交额 {_money(ticker['turnover'])}"
    )
    book = view["book"]
    st.caption(f"最优买 {_money(book['bid'])} · 最优卖 {_money(book['ask'])} · {book['note']}")
    st.caption("资金费历史是结算记录。")
    st.caption(status_sentence("历史记录"))
    if view["funding_history"]:
        st.dataframe(pd.DataFrame([{
            "资金费率": row["funding_rate"], "结算时间 UTC": _time_text(row["funding_time_ms"]),
        } for row in view["funding_history"]]), hide_index=True, width="stretch")
    if view["trades"]:
        st.dataframe(pd.DataFrame([{
            "价格": row["price"], "数量": row["size"], "方向": row["side"],
            "成交号": row["trade_id"], "成交时间 UTC": _time_text(row["exchange_time_ms"]),
        } for row in view["trades"]]), hide_index=True, width="stretch")


def _github(conn):
    st.caption("本地评分只根据公开元数据。不克隆、不安装、不运行这些仓库。")
    query = st.text_input("按仓库名过滤", key="repo_query")
    if st.button("刷新关注列表", type="primary"):
        for full_name in WATCH:
            try:
                repo, _used_token = fetch_repo(full_name)
                store.save_repo(conn, full_name, repo)
            except Exception as exc:
                cached = next((row for row in store.load_repos(conn) if row["full_name"] == full_name), None)
                if cached:
                    store.save_repo(conn, full_name, cached["payload"], error=str(exc))
                else:
                    st.error(f"{full_name} 不可用：{exc}")
    rows = []
    names = []
    for cached in store.load_repos(conn):
        repo = cached["payload"]
        if query.strip().lower() not in repo["full_name"].lower():
            continue
        scored = score_repo(repo)
        names.append(repo["full_name"])
        rows.append({
            "仓库": repo["full_name"], "许可证": repo["license_spdx"], "归档": "是" if repo["archived"] else "否",
            "星标": repo["stars"], "最近推送": repo["pushed_at"], "许可分": scored["license_score"],
            "维护分": scored["maintenance_score"], "质量分": scored["quality_score"],
            "安全分": "未扫描", "兼容分": scored["compatibility_score"],
            "链接": repo["html_url"], "说明": scored["license_note"],
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        for name in names:
            _favorite_button(conn, "repo", f"repo:{name}", name)
    else:
        st.info("还没有仓库缓存。刷新后才会请求 GitHub。可选环境变量 GITHUB_TOKEN 只从环境读取，不会写入页面或仓库。")
    st.caption("https://docs.github.com/en/rest/repos/repos")


def _library(conn):
    st.caption("研究者和文章只来自你在这台机器上的录入。自动中文摘要未接入。")
    query = st.text_input("搜索姓名、标题、链接或摘要")
    category = st.text_input("按分类过滤，留空为全部")
    left, right = st.columns(2)
    with left:
        st.markdown("**研究者**")
        name = st.text_input("姓名")
        researcher_category = st.text_input("分类", key="researcher_category")
        links = st.text_area("公开链接")
        notes = st.text_area("笔记")
        if st.button("保存研究者"):
            try:
                store.add_researcher(conn, name, researcher_category, links, notes)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with right:
        st.markdown("**文章**")
        title = st.text_input("标题")
        url = st.text_input("原文 URL")
        article_category = st.text_input("分类", key="article_category")
        summary = st.text_area("手工中文摘要")
        if st.button("保存文章"):
            try:
                store.add_article(conn, title, url, article_category, summary)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    researchers = store.list_researchers(conn, query)
    articles = store.list_articles(conn, query, category)
    if researchers:
        st.dataframe(pd.DataFrame(researchers), hide_index=True, width="stretch")
    else:
        st.info("研究者库是空的。")
    if articles:
        frame = pd.DataFrame(articles)
        st.dataframe(frame, hide_index=True, width="stretch")
        st.caption("摘要来源列应为“手工”。空摘要不是模型生成的总结。")
    else:
        st.info("文章库是空的。")


def _assistant():
    st.markdown("**本地规则说明**")
    st.write("这不是大模型。当前进程没有模型接口，不会编造研报、交易员或收益。")
    st.write("现货默认规则仍是 15 分钟收盘后的五项评分：4 小时和 1 小时同向、RSI 与 MACD、成交量、K 线位置、ATR 占比。下一根开盘才模拟成交。")
    st.write("均值回归只在选择该预设并在回测里运行“均值回归（研究）”时生效：已收盘价偏离布林带且 RSI 越过阈值，止损用 ATR 倍数，目标用布林中轨和外轨。")
    st.write("价格对照只比较 Bitget 公开最新价。价差不能把 B、C、D 升级成已验证持仓。")
    for grade, text in GRADES.items():
        st.caption(f"{grade} · {text}")


def _tasks(conn):
    sha = _git_sha()
    st.caption(f"研究库 schema 1 · Git {sha} · 不能在这里发布或合并。")
    title = st.text_input("任务")
    detail = st.text_area("说明")
    if st.button("添加任务"):
        try:
            store.add_task(conn, title, detail)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    for task in store.list_tasks(conn):
        st.write(f"{task['state']} · {task['title']}")
        if task["detail"]:
            st.caption(task["detail"])
        if task["state"] != "完成" and st.button("标为完成", key=f"task-{task['id']}"):
            store.set_task_state(conn, task["id"], "完成")
            st.rerun()
    st.dataframe(pd.DataFrame(MODULES)[["name", "state"]], hide_index=True, width="stretch")


def _positions(conn, positions, status):
    if not positions:
        st.info("这次响应里没有公开持仓。这不是一个示例账户。")
        return
    if not can_call_current(status):
        st.warning(status_sentence(status))
    checked = {}
    rows = []
    for position in positions[:8]:
        symbol = bitget_symbol_for(position.get("symbol") or "")
        comparison = "未对照"
        if symbol and symbol not in checked:
            try:
                price, _ = last_price(symbol)
                checked[symbol] = price
            except (MarketDataError, ValueError):
                checked[symbol] = None
        if symbol:
            result = crosscheck(position.get("mark") or position.get("mid"), checked.get(symbol))
            comparison = "无 Bitget 价格" if result["diff_pct"] is None else f"{result['diff_pct']:.3f}%"
        rows.append({
            "合约": position.get("symbol"), "方向": position.get("side"), "证据": position.get("grade", "B"),
            "开仓均价": position.get("entry"), "对照价格": position.get("mark", position.get("mid")),
            "杠杆": position.get("leverage"), "持仓量": position.get("size"),
            "持仓价值": position.get("position_value"), "未实现盈亏": position.get("unrealized_pnl"),
            "开仓时间": _time_text(position.get("open_time_ms")),
            "Bitget 价差 %": comparison,
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(crosscheck(None, None)["note"])


def _freshness(status, latency, exchange_ms, received_ms):
    latency_text = "缺失" if latency is None else f"{latency} ms"
    st.caption(
        f"状态 {status} · 行情时间 {_time_text(exchange_ms)} · 接收 {_time_text(received_ms)} · "
        f"接收减行情 {latency_text}"
    )
    st.caption(status_sentence(status))


def _favorite_button(conn, category, entity_key, label):
    enabled = store.is_favorite(conn, category, entity_key)
    verb = "移出自选" if enabled else "加入自选"
    if st.button(f"{verb} · {label}", key=f"fav-{category}-{entity_key}"):
        store.set_favorite(conn, category, entity_key, label, not enabled)
        st.rerun()


def _alerts(conn):
    rows = store.list_alerts(conn)
    if rows:
        st.markdown("**公开数据变化**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("这些是两次快照的差异，不是交易所成交通知，也不是社交帖提醒。")


def _previous_positions(conn, entity_key):
    rows = store.recent_snapshots(conn, entity_key, 1)
    if not rows:
        return None
    return rows[0]["payload"].get("positions") or []


def _probe_binance():
    for url in ("https://fapi.binance.com/fapi/v1/ping", "https://api.binance.com/api/v3/ping"):
        try:
            _payload, status = request_json("GET", url, attempts=1)
            return "已连接", f"{url} HTTP {status}"
        except Exception as exc:
            code = getattr(exc, "status", None)
            if code == 451:
                return "区域不可用", f"{url} HTTP 451。这只说明当前运行环境被拒绝，不代表所有者的网络同样不可用。"
            last = str(exc)
    return "连接失败", last


def _time_text(value):
    if value is None:
        return "缺失"
    return datetime.fromtimestamp(value / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _money(value):
    return "缺失" if value is None else f"{value:g}"


def _git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(__file__), text=True, timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "未知"
