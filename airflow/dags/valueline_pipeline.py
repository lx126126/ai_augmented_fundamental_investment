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

触发方式：
    - 手动：Web UI 点击 Trigger DAG，或传参数 {"code": "600519"}
    - 定时：每日凌晨 2 点（财报季按需改 schedule）
"""
from __future__ import annotations

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
# 跟踪池（默认标的，触发时可用 conf 覆盖 code）
# --------------------------------------------------------------------------- #
DEFAULT_CODE = "601088"


# --------------------------------------------------------------------------- #
# Task 1/4：拉取原始数据
# --------------------------------------------------------------------------- #
def fetch_data(code: str, **context) -> dict:
    """拉取单只股票真实财报 → 存 parquet。"""
    os.chdir(PROJECT_ROOT)
    from src.data.fetcher import fetch_all
    from src.data.storage import save_all

    print(f"[fetch] 拉取 {code} 财报数据 ...")
    data = fetch_all(code)
    paths = save_all(data, code)

    table_count = len(paths)
    print(f"[fetch] {code} 入库 {table_count} 张表")
    return {"code": code, "tables": table_count}


# --------------------------------------------------------------------------- #
# Task 2/4：数据质量 gate（交叉校验 + 造假检测）
# --------------------------------------------------------------------------- #
def validate_data(code: str, **context) -> dict:
    """数据质量 gate：PDF 金标准交叉校验 + Beneish 造假检测 + 审计意见。

    若造假检测判定高风险（含非标审计意见一票否决），抛异常触发告警回调，
    阻断下游 build，保证「脏数据不出报告」。
    """
    os.chdir(PROJECT_ROOT)
    import pandas as pd
    from src.validation import reconcile_balance_sheet, load_reconcile_log
    from src.analysis.fraud import fraud_check
    from src.data.adapter import load_raw
    from src.data.cleaner import build_annual_financials

    # 1) 官方年报 PDF 金标准交叉校验（覆盖接口错误字段，如神华 2025 总资产 9038→6278 亿）
    bs_path = Path("data/raw") / code / "balance_sheet.parquet"
    corrections = []
    if bs_path.exists():
        bs = pd.read_parquet(bs_path)
        d = pd.to_datetime(bs["report_date"])
        annual_dates = d[d.dt.month == 12]
        if not annual_dates.empty:
            year = int(annual_dates.dt.year.max())
            corrections = reconcile_balance_sheet(
                code, year,
                data_dir=Path("data/raw"), pdf_dir=Path("data/validation"),
            )
            if corrections:
                print(f"[validate] {code} {year} 用官方 PDF 金标准覆盖 {len(corrections)} 个接口错误字段")

    # 2) Beneish M-Score + 现金流背离 + 应收背离 + 审计意见
    raw = load_raw(code)
    required = {"financial_indicator", "profit_sheet", "balance_sheet", "cash_flow"}
    if not required.issubset(raw.keys()):
        raise FileNotFoundError(f"[validate] {code} 缺 parquet 表，请先运行 fetch")
    annual = build_annual_financials(raw)
    fraud = fraud_check(annual)
    risk = fraud.get("overall_risk")
    flags = fraud.get("flags", [])

    # 3) 质量 gate：高风险 → 阻断 + 告警
    if risk == "high":
        reason = "、".join(flags) or "未知原因"
        raise ValueError(f"[validate] {code} 财务造假检测高风险（{reason}），阻断下游报告生成")

    print(f"[validate] {code} 造假风险={risk}，警示项={flags or '无'}")
    return {
        "code": code,
        "corrections": len(corrections),
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
    _run_script("scripts/build_valueline.py", [code])
    return {"code": code, "status": "generated"}


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
    # 报告期由 build 落盘目录推导：取 reports/ 下最新的 {code}.html
    reports_root = Path(PROJECT_ROOT) / "reports"
    html_candidates = sorted(reports_root.glob(f"*/{code}.html"), key=lambda p: p.stat().st_mtime)
    if not html_candidates:
        print("[export] 未找到报告 HTML，跳过")
        return {"code": code, "status": "no_html"}

    html_path = html_candidates[-1]
    out_dir = html_path.parent
    try:
        _run_script("scripts/export.py", [str(html_path), "-o", str(out_dir), "-f", "png", "pdf"])
        return {"code": code, "status": "exported", "out": str(out_dir)}
    except Exception as e:
        # export.py 缺 playwright 时 sys.exit(1)；容器未装则降级跳过
        print(f"[export] 跳过（{e}）")
        return {"code": code, "status": "skipped"}


# --------------------------------------------------------------------------- #
# 告警回调：任务失败时记录日志 + 可选 Webhook 推送（钉钉/飞书/Slack）
# --------------------------------------------------------------------------- #
def notify_failure(context) -> None:
    """任务失败告警。日志始终记录；配置 AIRFLOW_ALERT_WEBHOOK 后追加推送。"""
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
            # 钉钉/飞书文本消息格式；Slack 等按需调整 payload
            requests.post(webhook, json={"msgtype": "text", "text": {"content": msg}}, timeout=5)
        except Exception as e:
            log.warning(f"[告警] webhook 推送失败：{e}")


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
    description="ValueLine 一页研报 ETL：拉取 → PDF金标准交叉校验+造假检测 → 渲染 → 数仓落库 → 导出",
    schedule="0 2 * * *",           # 每日凌晨 2 点；财报季可改为按需手动触发
    start_date=datetime(2026, 8, 1),
    catchup=False,
    max_active_runs=1,
    tags=["fundamental", "value-investing", "etl", "data-quality"],
    params={"code": DEFAULT_CODE},
) as dag:

    start = EmptyOperator(task_id="start")

    fetch = PythonOperator(
        task_id="fetch",
        python_callable=fetch_data,
        op_kwargs={"code": "{{ params.code }}"},
    )

    validate = PythonOperator(
        task_id="validate",
        python_callable=validate_data,
        op_kwargs={"code": "{{ params.code }}"},
    )

    build = PythonOperator(
        task_id="build",
        python_callable=build_report,
        op_kwargs={"code": "{{ params.code }}"},
    )

    warehouse = PythonOperator(
        task_id="warehouse",
        python_callable=refresh_warehouse,
        op_kwargs={"code": "{{ params.code }}"},
    )

    export = PythonOperator(
        task_id="export",
        python_callable=export_report,
        op_kwargs={"code": "{{ params.code }}"},
        trigger_rule="all_success",   # build 成功后才导出
    )

    # 血缘：fetch → validate（质量 gate 不过则阻断）→ build → warehouse → export
    start >> fetch >> validate >> build >> warehouse >> export
