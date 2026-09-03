# -*- coding: utf-8 -*-
"""ValueLine 一页研报 · 生产级 ETL 编排（Airflow DAG）。

数据流（血缘）：
    fetch      拉取原始财报（AKShare/东财/巨潮/腾讯）→ 落 data/raw/{code}/*.parquet
      ↓
    validate   数据质量 gate：官方年报 PDF 金标准交叉校验覆盖接口错误字段
               + Beneish M-Score 造假检测 + 现金流/应收背离 + 审计意见（非标一票否决）
               → 落 data/validation/{code}_{year}_reconcile.json
      ↓
    build      清洗宽表 + 组装模板结构 + LLM 叙事（只翻译数据不编数）→ 渲染 HTML
               → templates/valueline.html + reports/{报告期}/{code}.html
      ↓
    warehouse  数仓 schema 化落库：raw parquet → DuckDB（raw / mart 两层）
               → data/warehouse/fqf.duckdb（供 FastAPI 查询层直接 SELECT）
      ↓
    export     导出 PNG 长图 / A4 PDF（可选，需 playwright + chromium）

跟踪池：
    - 默认读 watchlist/watchlist.json 的 active 标的（全量更新）
    - 手动触发可用 conf {"codes": ["600519"]} 覆盖为指定股票

调度：
    - 财报季（1/4/7/10 月的 20 日前后，财报密集披露期）每日凌晨 2 点全量刷新
    - 非财报季不自动跑，按需手动触发（数据低频变化，避免空跑浪费）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 项目根挂载到容器 /opt/airflow/project；本地开发时可改为本机绝对路径
PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "/opt/airflow/project")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.empty import EmptyOperator


# --------------------------------------------------------------------------- #
# 跟踪池（默认读 watchlist.json 的 active 标的，手动触发可用 conf.codes 覆盖）
# --------------------------------------------------------------------------- #
DEFAULT_CODE = "601088"
WATCHLIST_PATH = Path(PROJECT_ROOT) / "watchlist" / "watchlist.json"


def resolve_codes(**context) -> list[str]:
    """解析本次运行要处理的股票代码列表（保留市场后缀，如 601088.SH / 09992.HK）。

    优先级：conf.codes（手动指定）> watchlist.json 的 active 标的 > DEFAULT_CODE。
    保留后缀以便 fetch/validate 区分 A 股（fetch_all）与港股（fetch_all_hk）。
    """
    conf = context.get("params") or {}
    codes = conf.get("codes")
    if codes:
        return [str(c) for c in codes]

    if WATCHLIST_PATH.exists():
        try:
            data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
            stocks = data.get("stocks", [])
            active = [s["code"] for s in stocks if s.get("status") == "active"]
            if active:
                return [str(c) for c in active]
        except (json.JSONDecodeError, OSError):
            pass

    return [DEFAULT_CODE]


def _is_hk(code: str) -> bool:
    """判断是否港股标的（带 .HK 后缀，或 0 开头 5 位码）。"""
    c = str(code).upper()
    if c.endswith(".HK"):
        return True
    bare = c.split(".")[0]
    return bare.startswith("0") and len(bare) == 5


def _store_code(code: str) -> str:
    """数据层键：港股剥 .HK 成 5 位码（09992），A 股剥后缀成 6 位码（601088）。"""
    from src.data.fetcher import _hk_code
    c = str(code).upper().split(".")[0]
    return _hk_code(c) if _is_hk(code) else c.zfill(6)


# --------------------------------------------------------------------------- #
# Task 1/4：拉取原始数据
# --------------------------------------------------------------------------- #
def fetch_data(code: str, **context) -> dict:
    """拉取单只股票真实财报 → 存 parquet（A 股走 fetch_all，港股走 fetch_all_hk）。"""
    os.chdir(PROJECT_ROOT)
    from src.data.fetcher import fetch_all, fetch_all_hk
    from src.data.storage import save_all

    is_hk = _is_hk(code)
    store_code = _store_code(code)
    print(f"[fetch] 拉取 {code} 财报数据（{'港股' if is_hk else 'A股'}）...")
    data = fetch_all_hk(code) if is_hk else fetch_all(code)
    paths = save_all(data, store_code)

    table_count = len(paths)
    print(f"[fetch] {code} 入库 {table_count} 张表")
    return {"code": store_code, "tables": table_count}


# --------------------------------------------------------------------------- #
# Task 2/4：数据质量 gate（交叉校验 + 造假检测）
# --------------------------------------------------------------------------- #
def validate_data(code: str, **context) -> dict:
    """数据质量 gate：PDF 金标准交叉校验 + Beneish 造假检测 + 审计意见。

    若造假检测判定高风险（含非标审计意见一票否决），抛异常触发告警回调，
    阻断下游 build，保证「脏数据不出报告」。
    港股暂无巨潮年报 PDF 金标准，跳过 reconcile，仅做造假检测。
    """
    os.chdir(PROJECT_ROOT)
    import pandas as pd
    from src.validation import reconcile_balance_sheet, load_reconcile_log
    from src.analysis.fraud import fraud_check
    from src.data.adapter import load_raw
    from src.data.cleaner import build_annual_financials

    is_hk = _is_hk(code)
    store_code = _store_code(code)

    # 1) 官方年报 PDF 金标准交叉校验（覆盖接口错误字段，如神华 2025 总资产 9038→6278 亿）
    #    港股暂无巨潮 PDF 金标准链路，跳过此步
    corrections = []
    if not is_hk:
        bs_path = Path("data/raw") / store_code / "balance_sheet.parquet"
        if bs_path.exists():
            bs = pd.read_parquet(bs_path)
            d = pd.to_datetime(bs["report_date"])
            annual_dates = d[d.dt.month == 12]
            if not annual_dates.empty:
                year = int(annual_dates.dt.year.max())
                corrections = reconcile_balance_sheet(
                    store_code, year,
                    data_dir=Path("data/raw"), pdf_dir=Path("data/validation"),
                )
                if corrections:
                    print(f"[validate] {store_code} {year} 用官方 PDF 金标准覆盖 {len(corrections)} 个接口错误字段")

    # 2) 基础数据质量 gate：行数/空值/正数/会计勾稽（防接口空表、脏数据）
    #    在造假检测之前先校验「数据是否完整、数值是否合理」，失败即阻断下游。
    raw = load_raw(store_code)
    required = {"financial_indicator", "profit_sheet", "balance_sheet", "cash_flow"}
    if not required.issubset(raw.keys()):
        raise FileNotFoundError(f"[validate] {store_code} 缺 parquet 表，请先运行 fetch")

    from src.data.quality import validate_all
    qc = validate_all(raw)
    if not qc.ok:
        raise ValueError(f"[validate] {store_code} 数据质量校验未通过：\n{qc.summary()}")
    print(f"[validate] {store_code} 数据质量校验通过（{qc.passed} 项断言）")

    # 3) Beneish M-Score + 现金流背离 + 应收背离 + 审计意见
    annual = build_annual_financials(raw)
    fraud = fraud_check(annual)
    risk = fraud.get("overall_risk")
    flags = fraud.get("flags", [])

    # 4) 质量 gate：高风险 → 阻断 + 告警
    if risk == "high":
        reason = "、".join(flags) or "未知原因"
        raise ValueError(f"[validate] {store_code} 财务造假检测高风险（{reason}），阻断下游报告生成")

    print(f"[validate] {store_code} 造假风险={risk}，警示项={flags or '无'}")
    return {
        "code": store_code,
        "corrections": len(corrections),
        "quality_checks": qc.passed,
        "fraud_risk": risk,
        "fraud_flags": flags,
    }


# --------------------------------------------------------------------------- #
# 工具：在项目根运行脚本（build/export 脚本顶层含 `from _sample_data import`，
# 直接 import 会因 scripts/ 非包而失败，故用 subprocess 以命令行方式调用）
# --------------------------------------------------------------------------- #
def _run_script(script: str, args: list[str]) -> None:
    """运行项目脚本，输出进 Airflow 日志，失败抛异常（触发重试/告警）。"""
    import subprocess

    cmd = [sys.executable, script, *args]
    print(f"[run] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


# --------------------------------------------------------------------------- #
# Task 3/4：渲染报告
# --------------------------------------------------------------------------- #
def build_report(code: str, **context) -> dict:
    """读 parquet → 清洗宽表 → 组装模板结构 → LLM 叙事 → 渲染 HTML。"""
    store_code = _store_code(code)
    _run_script("scripts/build_valueline.py", [store_code])
    return {"code": store_code, "status": "generated"}


# --------------------------------------------------------------------------- #
# Task 4/5：数仓 schema 化落库（raw parquet → DuckDB）
# --------------------------------------------------------------------------- #
def refresh_warehouse(code: str, **context) -> dict:
    """刷新 DuckDB 数仓：raw 挂载 + mart 宽表落库。

    全量重建（跨所有已拉取股票），保证数据一致；DuckDB 单文件零运维。
    供 FastAPI 查询层直接 SELECT，避免每次重算宽表。
    """
    os.chdir(PROJECT_ROOT)
    from src.data.warehouse import build_warehouse

    summary = build_warehouse()
    mart_counts = summary.get("mart", {})
    print(f"[warehouse] 数仓已刷新，mart 表行数 = {mart_counts}")
    return {"code": code, "mart": mart_counts}


# --------------------------------------------------------------------------- #
# Task 5/5：导出 PNG 长图 / PDF（可选，需 playwright）
# --------------------------------------------------------------------------- #
def export_report(code: str, **context) -> dict:
    """导出高清 PNG 长图 / A4 PDF（容器未装 playwright 时优雅降级跳过）。"""
    store_code = _store_code(code)
    # 报告期由 build 落盘目录推导：取 reports/ 下最新的 {code}.html
    reports_root = Path(PROJECT_ROOT) / "reports"
    html_candidates = sorted(reports_root.glob(f"*/{store_code}.html"), key=lambda p: p.stat().st_mtime)
    if not html_candidates:
        print("[export] 未找到报告 HTML，跳过")
        return {"code": store_code, "status": "no_html"}

    html_path = html_candidates[-1]
    out_dir = html_path.parent
    try:
        _run_script("scripts/export.py", [str(html_path), "-o", str(out_dir), "-f", "png", "pdf"])
        return {"code": store_code, "status": "exported", "out": str(out_dir)}
    except Exception as e:
        # export.py 缺 playwright 时 sys.exit(1)；容器未装则降级跳过
        print(f"[export] 跳过（{e}）")
        return {"code": store_code, "status": "skipped"}


# --------------------------------------------------------------------------- #
# Task 6/6：生成手机网页版首页（数据驱动，扫描 reports/ 最新报告）
# --------------------------------------------------------------------------- #
def build_web_index(**context) -> dict:
    """重新生成 web/index.html（扫描 reports/ 归档 + 跟踪池名称映射）。"""
    os.chdir(PROJECT_ROOT)
    from scripts.build_web_index import main as _build_index

    _build_index()
    return {"status": "generated"}


# --------------------------------------------------------------------------- #
# 告警回调：任务失败时记录日志 + Webhook 推送（个人微信 Server酱/PushPlus，或钉钉/飞书）
# --------------------------------------------------------------------------- #
def _push_alert(msg: str) -> None:
    """按环境变量推送到对应渠道。

    支持三种渠道（按优先级，配置哪个用哪个）：
    - SERVERCHAN_SENDKEY：Server 酱（推送到个人微信，无需企业微信）
      POST https://sctapi.ftqq.com/{key}.send  title/desp
    - PUSHPLUS_TOKEN：PushPlus（推送到个人微信）
      POST https://www.pushplus.plus/send  token/title/content
    - AIRFLOW_ALERT_WEBHOOK：通用 webhook（钉钉/飞书/企业微信机器人文本格式）
    """
    import requests

    key = os.environ.get("SERVERCHAN_SENDKEY")
    if key:
        r = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": "fqf 数据管道告警", "desp": msg},
            timeout=10,
        )
        print(f"[alert] Server酱 推送状态 {r.status_code}")
        return

    token = os.environ.get("PUSHPLUS_TOKEN")
    if token:
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": "fqf 数据管道告警", "content": msg, "template": "txt"},
            timeout=10,
        )
        print(f"[alert] PushPlus 推送状态 {r.status_code}")
        return

    webhook = os.environ.get("AIRFLOW_ALERT_WEBHOOK")
    if webhook:
        r = requests.post(webhook, json={"msgtype": "text", "text": {"content": msg}}, timeout=10)
        print(f"[alert] webhook 推送状态 {r.status_code}")
        return

    print("[alert] 未配置告警渠道（SERVERCHAN_SENDKEY / PUSHPLUS_TOKEN / AIRFLOW_ALERT_WEBHOOK），仅记录日志")


def notify_failure(context) -> None:
    """任务失败告警。日志始终记录；配置推送渠道后追加推送（个人微信等）。"""
    import logging

    log = context.get("log", logging.getLogger("airflow.task"))
    dag = context.get("dag")
    ti = context.get("task_instance")
    exc = context.get("exception")
    msg = (
        f"DAG `{dag.dag_id}` 任务 `{ti.task_id}` 失败\n"
        f"execution_date={context.get('execution_date')}\n"
        f"exception={exc}"
    )
    log.error(msg)

    try:
        _push_alert(msg)
    except Exception as e:
        log.warning(f"[告警] 推送失败：{e}")


# --------------------------------------------------------------------------- #
# DAG 定义
# --------------------------------------------------------------------------- #
default_args = {
    "owner": "li_xiao",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": notify_failure,
}

with DAG(
    dag_id="valueline_pipeline",
    default_args=default_args,
    description="ValueLine 一页研报 ETL：拉取 → PDF金标准交叉校验+造假检测 → 渲染 → 数仓落库 → 导出（跟踪池全量）",
    # 财报季（1/4/7/10 月的 20-31 日）每日凌晨 2 点全量刷新；非财报季不自动跑，按需手动触发
    schedule="0 2 20-31 1,4,7,10 *",
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["fundamental", "value-investing", "etl", "data-quality"],
    params={"codes": []},
) as dag:

    start = EmptyOperator(task_id="start")

    # 跟踪池标的（DAG 解析时从 watchlist.json 读取；运行期可用 conf.codes 覆盖）
    _codes = resolve_codes(params={"codes": []})

    # 逐票生成 fetch → validate → build → export 链；warehouse 在所有票 build 后统一跑一次
    last_build = []
    for code in _codes:
        # task_id 用无后缀的 store_code（Airflow task_id 不能含 . 等特殊字符）
        sc = _store_code(code)
        fetch = PythonOperator(
            task_id=f"fetch_{sc}",
            python_callable=fetch_data,
            op_kwargs={"code": code},
        )
        validate = PythonOperator(
            task_id=f"validate_{sc}",
            python_callable=validate_data,
            op_kwargs={"code": code},
        )
        build = PythonOperator(
            task_id=f"build_{sc}",
            python_callable=build_report,
            op_kwargs={"code": code},
        )
        export = PythonOperator(
            task_id=f"export_{sc}",
            python_callable=export_report,
            op_kwargs={"code": code},
            trigger_rule="all_success",   # build 成功后才导出
        )
        start >> fetch >> validate >> build >> export
        last_build.append(build)

    warehouse = PythonOperator(
        task_id="warehouse",
        python_callable=refresh_warehouse,
        op_kwargs={"code": "all"},   # warehouse 全量重建，不依赖单票 code
    )

    # 所有票 build 完成后统一刷新数仓（DuckDB 单文件，全量重建）
    last_build >> warehouse

    # 生成手机网页版首页（数据驱动，扫描 reports/ 最新报告）
    web_index = PythonOperator(
        task_id="web_index",
        python_callable=build_web_index,
    )
    last_build >> web_index
