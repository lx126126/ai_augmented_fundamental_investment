# -*- coding: utf-8 -*-
"""每日行情刷新 ETL（Airflow DAG）。

与财报季 DAG（valueline_pipeline）的分工：
    - 财报季 DAG：拉财务三表 + 分红 + 分业务（低频，季度），跑 PDF 金标准校验 + 造假检测 + LLM 叙事；
    - 本 DAG：只拉「行情类」数据（现价/PE/PB/市值/52周/机构评级，高频，每交易日），
      落历史快照表 + 覆盖报告估值板块，不碰三表、不跑 Beneish、不跑 LLM。

数据流（血缘）：
    snapshot  拉腾讯行情 + 百度估值 + 东财评级 → 落 data/market/{code}_*.parquet（历史快照）
              + 覆盖 data/raw/{code}/quote|valuation|rating.parquet（报告用最新表）
      ↓
    refresh   轻量重刷报告（--daily：跳过 PDF 校验 + LLM，只更新估值与市场板块）
      ↓
    web_index 重刷手机网页版首页（扫描 reports/ 最新报告）

调度：
    - 交易日（周一至周五）收盘后 15:30（A 股收盘 15:00，港股 16:00 收盘，取 16:30 兼顾）
    - 节假日会空跑（接口返回昨日收盘价，无害），可选接入交易日历优化
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "/opt/airflow/project")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.empty import EmptyOperator


WATCHLIST_PATH = Path(PROJECT_ROOT) / "watchlist" / "watchlist.json"


def _resolve_watchlist() -> list[dict]:
    """读取 watchlist.json 的 active 标的，保留市场后缀（区分 A 股 / 港股）。

    返回 [{"code": "601088", "market": "sh"}, {"code": "09992", "market": "hk"}, ...]
    """
    if not WATCHLIST_PATH.exists():
        return []
    try:
        data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        stocks = data.get("stocks", [])
    except (json.JSONDecodeError, OSError):
        return []

    out = []
    for s in stocks:
        if s.get("status") != "active":
            continue
        raw = s["code"]  # 形如 "601088.SH" / "09992.HK"
        if "." in raw:
            code, suffix = raw.rsplit(".", 1)
        else:
            code, suffix = raw, "SH"
        # 市场后缀标准化：SH/SZ → 腾讯行情用 sh/sz；HK → hk
        market = {"SH": "sh", "SZ": "sz", "BJ": "bj", "HK": "hk"}.get(suffix.upper(), None)
        out.append({"code": code, "market": market, "name": s.get("name", code)})
    return out


def snapshot_market(**context) -> dict:
    """逐票拉行情快照（港股只拉行情，不拉估值/评级）。"""
    os.chdir(PROJECT_ROOT)
    from src.data.market_snapshot import snapshot_all

    stocks = _resolve_watchlist()
    if not stocks:
        print("[market] watchlist 无 active 标的，跳过")
        return {"count": 0, "detail": []}

    detail = []
    for s in stocks:
        code, market, name = s["code"], s["market"], s["name"]
        try:
            r = snapshot_all(code, market=market)
            r["name"] = name
            detail.append(r)
            flags = []
            if r["quote"]:
                flags.append("行情")
            if r["valuation"]:
                flags.append("估值")
            if r["rating"]:
                flags.append("评级")
            print(f"[market] {code} {name}：{'、'.join(flags) or '全部失败'}")
        except Exception as e:
            print(f"[market] {code} {name} 失败：{type(e).__name__}: {e}")
            detail.append({"code": code, "name": name, "error": str(e)})

    return {"count": len(stocks), "detail": detail}


def refresh_report(code: str, **context) -> dict:
    """轻量重刷单票报告（--daily 模式：跳过 PDF 校验 + LLM，只更新估值板块）。"""
    os.chdir(PROJECT_ROOT)
    import subprocess

    cmd = [sys.executable, "scripts/build_valueline.py", code, "--daily"]
    print(f"[refresh] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
    return {"code": code, "status": "refreshed"}


def refresh_web_index(**context) -> dict:
    """重刷手机网页版首页。"""
    os.chdir(PROJECT_ROOT)
    from scripts.build_web_index import main as _build_index

    _build_index()
    return {"status": "generated"}


def notify_failure(context) -> None:
    """任务失败告警（日志 + 可选 Webhook）。"""
    import logging

    log = context.get("log", logging.getLogger("airflow.task"))
    dag = context.get("dag")
    ti = context.get("task_instance")
    msg = (
        f"[告警] DAG `{dag.dag_id}` 任务 `{ti.task_id}` 失败\n"
        f"execution_date={context.get('execution_date')}\n"
        f"exception={context.get('exception')}"
    )
    log.error(msg)

    webhook = os.environ.get("AIRFLOW_ALERT_WEBHOOK")
    if webhook:
        try:
            import requests
            requests.post(webhook, json={"msgtype": "text", "text": {"content": msg}}, timeout=5)
        except Exception as e:
            log.warning(f"[告警] webhook 推送失败：{e}")


default_args = {
    "owner": "li_xiao",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": notify_failure,
}

with DAG(
    dag_id="market_daily",
    default_args=default_args,
    description="每日行情刷新：拉行情/估值/评级 → 落历史快照 → 轻量重刷报告估值板块 → 重刷网页首页",
    # 交易日收盘后 16:30（兼顾 A 股 15:00 与港股 16:00 收盘）
    schedule="30 16 * * 1-5",
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["market", "quote", "valuation", "daily"],
) as dag:

    start = EmptyOperator(task_id="start")

    snapshot = PythonOperator(
        task_id="snapshot_market",
        python_callable=snapshot_market,
    )

    _stocks = _resolve_watchlist()

    refresh_tasks = []
    for s in _stocks:
        code = s["code"]
        refresh = PythonOperator(
            task_id=f"refresh_{code}",
            python_callable=refresh_report,
            op_kwargs={"code": code},
        )
        refresh_tasks.append(refresh)

    web_index = PythonOperator(
        task_id="web_index",
        python_callable=refresh_web_index,
        trigger_rule="all_done",  # 部分票失败不影响首页生成
    )

    start >> snapshot >> refresh_tasks >> web_index
