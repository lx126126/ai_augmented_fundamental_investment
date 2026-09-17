#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fqf 报告服务：输入代码或名称 → 出一页报告（按需生成 + 缓存）。

与 src/api/ 的分工（两者都是 FastAPI，但职责不同，别混）
---------------------------------------------------------
    src/api/        数据查询 API —— 只读 DuckDB mart 层，返回 JSON。
                    对齐「后端 FastAPI 查询 API」的岗位能力证明。
    web/server.py   产品服务   —— 面向「查一只股票、出一页报告」的使用场景，
                    返回 HTML、能触发按需取数与报告生成。

架构（三层，各层频率与成本差一个量级，因此解耦）
------------------------------------------------
    L0 索引（全市场 8368 只）      秒级可查     scripts/build_market_index.py   每周
    L1 行情（全市场快照）          15.7s/全市场  scripts/update_spot_all.py      每交易日
    L2 财报 + 报告（单标的、昂贵）  首次 ~1.5 分钟 scripts/update_financials.py    每季度/按需
本服务处在 L2：**查谁拉谁、算完缓存**，因此数据范围不受"预拉过什么"限制。

🔴 本服务最重要的一个设计：绝不生成假报告
------------------------------------------
`build_valueline._load_real_data()` 在取数失败时会**静默降级成示例数据**
（变量 `data_src = "示例数据"`，用的是中国神华的硬编码样例），并且照样输出
一份"看起来完全正常"的报告。命令行里这算兜底，产品里这等于**给用户看别人的财报**。
所以本服务在生成前显式校验数据是否真的存在（`_has_data`），缺就先拉、拉不到就明确报错。

启动
----
    # 本机访问
    python -m uvicorn web.server:app --host 127.0.0.1 --port 8000

    # 手机同一 WiFi 访问（用本机内网 IP，如 http://192.168.1.5:8000）
    python -m uvicorn web.server:app --host 0.0.0.0 --port 8000
    ⚠️ 0.0.0.0 会把服务暴露给同网段：家里 WiFi 可用，公共网络别开。

    # 若所在环境必须经代理出网（沙箱/CI/海外云）
    FQF_HTTP_PROXY=http://127.0.0.1:15236 python -m uvicorn web.server:app ...
"""
from __future__ import annotations

import sys
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))  # build_valueline 里有裸导入 `from _sample_data import ...`

from src.data import market_index  # noqa: E402
from src.data.fetcher import fetch_all, fetch_all_hk  # noqa: E402
from src.data.storage import save_all  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
REPORTS_DIR = ROOT / "reports"
WEB_DIR = ROOT / "web"

#: 判定「有数据可出报告」的最低表集合（缺任一张，报告的关键板块会空）
REQUIRED_TABLES = ("profit_sheet", "balance_sheet", "cash_flow")

#: 生成串行 —— build_valueline 会写固定的 templates/valueline.html，
#: 并发调用会互相踩踏；同时串行也天然避免了数据源限流。
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report")

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

app = FastAPI(
    title="fqf 一页报告服务",
    description="输入代码或名称 → 出 ValueLine 风格一页报告（按需取数 + 缓存）",
    version="1.0.0",
)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #

def _store_code(symbol: str, market: str) -> str:
    """目录名形态：A 股 6 位、港股 5 位（与 data/raw 目录、报告归档名一致）。"""
    return str(symbol).strip().zfill(5 if str(market).upper() == "HK" else 6)


def _has_data(code: str) -> bool:
    """该标的是否已有可用于生成报告的数据（目录存在 + 三张关键表都有行）。"""
    d = RAW_DIR / code
    if not d.is_dir():
        return False
    for t in REQUIRED_TABLES:
        p = d / f"{t}.parquet"
        if not p.exists() or p.stat().st_size < 200:  # 空表/坏表
            return False
    return True


def _find_report(code: str) -> Path | None:
    """找该标的最新报告文件（reports/{期}/{code}.html）。"""
    cands = list(REPORTS_DIR.glob(f"*/{code}.html"))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _report_fresh(code: str) -> bool:
    """报告是否比它的数据新（数据没变就不用重算，省时省 token）。"""
    p = _find_report(code)
    if not p:
        return False
    d = RAW_DIR / code
    if not d.is_dir():
        return False
    newest_raw = max((f.stat().st_mtime for f in d.glob("*.parquet")), default=0.0)
    return p.stat().st_mtime >= newest_raw


def _fetch_financials(code: str, market: str) -> int:
    """按需拉财报并落盘，返回落盘的表数。"""
    data = fetch_all_hk(code) if str(market).upper() == "HK" else fetch_all(code)
    if not data:
        return 0
    save_all(data, code)
    return len(data)


def _build_report(code: str) -> Path:
    """生成报告（延迟导入 build_valueline，避免服务启动就加载整套模板）。"""
    import build_valueline  # noqa: PLC0415  延迟导入是刻意的：它模块级会加载模板/样式
    build_valueline.build(code)
    p = _find_report(code)
    if not p:
        raise RuntimeError("报告生成结束但未找到产物文件（reports/*/{code}.html）")
    return p


def _set(job_id: str, state: str, step: str, pct: int, **extra) -> None:
    with _jobs_lock:
        j = _jobs.get(job_id)
        if j is None:
            return
        j.update({"state": state, "step": step, "pct": pct,
                  "updated_at": datetime.now().strftime("%H:%M:%S")})
        j.update(extra)


def _run_job(job_id: str, code: str, market: str) -> None:
    """后台生成流程。任何异常都转成 job 的 error 状态，绝不吞掉。"""
    try:
        if not _has_data(code):
            _set(job_id, "fetching", "首次查询该标的，正在拉取财报数据（30-60 秒）…", 10)
            n = _fetch_financials(code, market)
            if not _has_data(code):
                raise RuntimeError(
                    f"财报数据拉取后仍不可用（落到 {n} 张表）—— "
                    "可能是数据源临时不可用，或该代码无财报数据"
                )
        else:
            _set(job_id, "fetching", "本地已有财报数据，跳过拉取", 40)

        # 实测 600036 全流程约 4 分钟（拉数 1 分钟 + LLM 叙事 2-3 分钟 + 年报 PDF 校验），
        # 所以这里如实写 2-4 分钟，不要写"20-60 秒"让用户以为卡死了。
        _set(job_id, "building",
             "正在生成一页报告（LLM 叙事 + 年报 PDF 交叉校验，约 2-4 分钟）…", 70)
        p = _build_report(code)

        _set(job_id, "done", "报告已生成", 100,
             report=str(p.relative_to(ROOT)), code=code, market=market,
             finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        _set(job_id, "error", f"生成失败：{type(e).__name__}: {e}", 0,
             error_detail=traceback.format_exc()[-1200:])
        print(f"[report] job={job_id} code={code} 失败：{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """查询首页（web/query.html）。"""
    p = WEB_DIR / "query.html"
    if not p.exists():
        return HTMLResponse(
            "<h1>fqf 一页报告服务</h1>"
            "<p>缺少 web/query.html，可直接调用 API：</p>"
            "<ul><li>GET /api/search?q=茅台</li>"
            "<li>POST /api/report {\"query\": \"600519\"}</li>"
            "<li>GET /api/report/status/{job_id}</li>"
            "<li>GET /report/600519</li></ul>"
        )
    return HTMLResponse(p.read_text(encoding="utf-8"))


@app.get("/report/{code}", response_class=HTMLResponse)
def view_report(code: str) -> HTMLResponse:
    """查看已生成的报告 HTML。

    代码规范化：按用户原样 → 补 5 位（港股）→ 补 6 位（A 股）依次尝试。
    **先试原样**很重要：600036 若直接补 5 位会变成 00036（不符规范）甚至撞到别的标的。
    """
    c = str(code).strip().upper().split(".")[0]
    candidates = [c] if not c.isdigit() else [c, c.zfill(5), c.zfill(6)]
    for x in candidates:
        p = _find_report(x)
        if p:
            return HTMLResponse(p.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail=f"暂无 {code} 的报告，请先生成")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/api/health")
def health() -> dict:
    idx = market_index.INDEX_PATH
    n_reports = len(list(REPORTS_DIR.glob("*/*.html"))) if REPORTS_DIR.exists() else 0
    return {
        "ok": True,
        "index": {"path": str(idx.relative_to(ROOT)), "exists": idx.exists(),
                  **market_index.stats()},
        "reports": n_reports,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, description="代码或名称，如 600519 / 茅台 / 00700"),
           limit: int = Query(20, ge=1, le=50)) -> dict:
    """全市场标的搜索（代码前缀 / 名称包含）。"""
    try:
        hits = market_index.search(q, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"索引不可用：{e}")
    for h in hits:
        code = h["symbol"]
        h["has_data"] = _has_data(code)
        h["has_report"] = _find_report(code) is not None
    return {"query": q, "count": len(hits), "results": hits}


@app.get("/api/reports")
def list_reports() -> dict:
    """已生成的报告清单（按修改时间倒序）。"""
    if not REPORTS_DIR.exists():
        return {"count": 0, "results": []}
    files = sorted(REPORTS_DIR.glob("*/*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    for p in files[:200]:
        code = p.stem
        results.append({
            "code": code,
            "period": p.parent.name,
            "url": f"/report/{code}",
            "size_kb": round(p.stat().st_size / 1024, 1),
            "generated_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return {"count": len(results), "results": results}


@app.post("/api/report")
def create_report(payload: dict) -> dict:
    """提交报告生成任务。

    body: {"query": "600519"} 或 {"query": "茅台"}
    多命中时**不猜**，返回 candidates 让前端让用户选（避免查 A 生成 B 的报告）。
    """
    q = str(payload.get("query", "")).strip()
    if not q:
        raise HTTPException(status_code=400, detail="缺少 query 参数")

    try:
        hit = market_index.resolve(q)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"索引不可用：{e}")

    if hit is None:
        cands = market_index.search(q, limit=10)
        if not cands:
            raise HTTPException(status_code=404, detail=f"全市场索引里找不到「{q}」")
        # 候选也带上状态，前端才能显示「已有报告 / 可生成 / 首次生成」
        for c in cands:
            cc = _store_code(c["symbol"], c["market"])
            c["has_data"] = _has_data(cc)
            c["has_report"] = _find_report(cc) is not None
        return JSONResponse({
            "state": "ambiguous",
            "message": f"「{q}」匹配到 {len(cands)} 个标的，请选择具体代码",
            "candidates": cands,
        })

    code = _store_code(hit["symbol"], hit["market"])

    # 已有且新鲜 → 不必重算
    if _report_fresh(code):
        p = _find_report(code)
        return {"state": "cached", "code": code, "name": hit["name"],
                "market": hit["market"], "report": str(p.relative_to(ROOT)),
                "url": f"/report/{code}"}

    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id, "code": code, "name": hit["name"], "market": hit["market"],
            "state": "queued", "step": "任务已排队", "pct": 0,
            "created_at": datetime.now().strftime("%H:%M:%S"),
        }
    _pool.submit(_run_job, job_id, code, hit["market"])
    return {"state": "queued", "job_id": job_id, "code": code,
            "name": hit["name"], "market": hit["market"], "url": f"/report/{code}"}


@app.get("/api/report/status/{job_id}")
def report_status(job_id: str) -> dict:
    """轮询生成进度。"""
    with _jobs_lock:
        j = _jobs.get(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
        out = dict(j)
    if out.get("state") == "done":
        out["url"] = f"/report/{out['code']}"
    return out


if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="fqf 一页报告服务")
    ap.add_argument("--host", default="127.0.0.1", help="绑定地址（手机访问用 0.0.0.0）")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("web.server:app", host=a.host, port=a.port, reload=a.reload)
