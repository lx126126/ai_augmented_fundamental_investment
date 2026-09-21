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

🔴 生成报告即入跟踪池（2026-09-17）
------------------------------------
报告生成成功后自动 `watchlist_store.add(code)`，并异步重建 `web/watchlist.html`。
理由：跟踪池原先靠手维护 `watchlist.json`，结果是「报告生成了、对比表里却没有」——
实测 11 份报告里只有 6 只在池中，用户明确要求「生成一个公司的报告就把它加进对比」。

重建走独立的单线程池（`_derived_pool`）：对比表要逐只跑 `build_template_data`，
首次约 14 秒/只，不能占住报告队列（那是 max_workers=1 的串行队列，会拖住后续查询）。
重建失败**不影响报告**，只在 `/api/refresh-status` 里留下错误。

🔴 本服务最重要的一个设计：绝不生成假报告
------------------------------------------
`build_valueline._load_real_data()` 在取数失败时会**静默降级成示例数据**
（变量 `data_src = "示例数据"`，用的是中国神华的硬编码样例），并且照样输出
一份"看起来完全正常"的报告。命令行里这算兜底，产品里这等于**给用户看别人的财报**。
所以本服务在生成前显式校验数据是否真的存在（`_has_data`），缺就先拉、拉不到就明确报错。

页面路由（2026-09-17 收敛为一个首页）
---------------------------------------
    /                    首页 = 搜索框 + 已生成报告列表 + 横向对比入口（web/index.html）
    /home  /index.html  /web/index.html   同上（别名：/home 是旧链接；另两个供页面间
                          相对路径互链 —— 首页互链用 `index.html`、报告页回首页用
                          `../../web/index.html`，服务模式下分别解析成这两个路径）
    /watchlist  /watchlist.html   跟踪池横向对比表
    /report/{code}       单个标的一页报告
    /reports/**          报告原文件静态挂载（首页卡片的相对链接落到这里）

「什么算一份报告」由 `src/report/artifacts.py` 统一定义（报告期目录 + 取最新期 +
排除 reports/xhs 发布包）。本文件不再自己 glob 报告文件。

旧 `web/query.html`（独立搜索页）**不再是产品入口**，但**文件与路由都保留**：
它的能力已被首页完整覆盖（两处搜索入口 = 两份同功能 JS = 必然漂移），
所以首页上不再有任何指向它的入口；路由留着的唯一理由是**防旧书签 404**
（她手机上的旧链接 / 浏览器历史点进来还能用）。见 `docs/mobile-web.md`。

⚠️ 别把「不再是入口」读成「可以删掉」—— 2026-09-18 曾按「已删除」处理差点删了它。
真要删得同时改 `/query`、`/query.html` 两个路由与 `docs/mobile-web.md` 那一行。

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
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))  # build_valueline 里有裸导入 `from _sample_data import ...`

from src.data import market_index, watchlist_store  # noqa: E402
from src.data.fetcher import fetch_all, fetch_all_hk  # noqa: E402
from src.data.storage import save_all  # noqa: E402
from src.report import artifacts  # noqa: E402

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

#: 派生产物（首页 + 跟踪池对比表）重建 —— 独立线程池，避免拖住串行的报告队列
_derived_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="derived")
_derived_lock = threading.Lock()
_derived_state: dict = {"running": False, "queued": False,
                        "last_ok": None, "last_at": None, "last_err": None}

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
    """找该标的的最新报告文件。

    🔴 判据统一在 `src/report/artifacts.py`，这里不再自己 glob —— 原先用的是
    `reports.glob("*/{code}.html")` 取 mtime 最大，会把 `reports/xhs/600519.html`
    （小红书九宫格发布包）当成报告；而 `build_web_index` 用的是「跳过 xhs + 按报告期
    取最新」。两处口径不同，迟早对不上（详见该模块 docstring）。
    """
    return artifacts.find(code)


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


#: 需要在新报告生成后重建的派生产物：(脚本, 超时秒)。顺序即执行顺序。
#: build_web_index 很快（读 parquet + 写 HTML，1-2 秒）；build_watchlist 逐只跑
#: build_template_data（缓存命中则很快，全量失效时约 2.5 分钟），所以超时给得宽。
_DERIVED_STEPS = (("build_web_index.py", 300), ("build_watchlist.py", 1800))


def _rebuild_derived() -> None:
    """重建派生产物：首页 + 跟踪池对比表。失败只记状态，绝不抛出（不该影响报告）。

    🔴 首页也必须在里面。只重建对比表的话，用户生成完一份新报告回到首页，
    列表里仍然没有它（首页是构建产物）——「生成即入池」的承诺就断在最后一步。
    """
    import subprocess  # noqa: PLC0415
    with _derived_lock:
        _derived_state.update(queued=False, running=True)

    ok_all, errs = True, []
    for script, timeout in _DERIVED_STEPS:
        try:
            r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
            good = r.returncode == 0
            print(f"[derived] {script} {'成功' if good else '失败'}："
                  f"{(r.stdout or '').strip()[-160:]}")
            if not good:
                errs.append(f"{script}: {(r.stderr or '').strip()[-200:]}")
        except Exception as e:
            good = False
            errs.append(f"{script}: {type(e).__name__}: {e}")
            print(f"[derived] {script} 异常：{type(e).__name__}: {e}")
        ok_all = ok_all and good

    with _derived_lock:
        _derived_state.update(
            last_ok=ok_all,
            last_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            last_err=None if ok_all else " | ".join(errs)[-400:],
            running=False,
        )


def _schedule_derived_rebuild() -> bool:
    """提交一次派生产物重建（已有排队/在跑则合并，避免连点查询时反复重算）。"""
    with _derived_lock:
        if _derived_state["queued"] or _derived_state["running"]:
            return False
        _derived_state["queued"] = True
    _derived_pool.submit(_rebuild_derived)
    return True


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

        # 生成成功即入跟踪池，并异步重建对比表 —— 见模块 docstring
        with _jobs_lock:
            name = (_jobs.get(job_id) or {}).get("name")
        # 报告生成时 `build_valueline._sync_watchlist()` 已按**报告口径**回写过一次
        # （Lynch 分类以报告为准）。这里**再兜一次**是刻意的：那条回写被 try 包着、
        # 失败只打印，如果它失败了而这里也不管，就会出现「报告生成成功、对比表里却没有」
        # —— 正是 2026-09-17 要修的那个问题。`upsert_from_report` 幂等，重复调用无副作用。
        try:
            status = watchlist_store.upsert_from_report(code, name=name)
        except Exception as e:  # 入池失败不该让"报告已生成"变成失败
            status = "skipped"
            print(f"[watchlist] {code} 入池失败（不影响报告）：{type(e).__name__}: {e}")
        in_pool = watchlist_store.get(code) is not None
        queued = _schedule_derived_rebuild()

        _set(job_id, "done",
             "报告已生成，已加入跟踪池（首页与对比表重建中…）" if queued
             else "报告已生成，已加入跟踪池", 100,
             report=str(p.relative_to(ROOT)), code=code, market=market,
             watchlist_added=in_pool, watchlist_status=status,
             watchlist_rebuild_queued=queued,
             finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        _set(job_id, "error", f"生成失败：{type(e).__name__}: {e}", 0,
             error_detail=traceback.format_exc()[-1200:])
        print(f"[report] job={job_id} code={code} 失败：{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #

def _serve_html(path: Path, hint: str) -> HTMLResponse:
    """把一个构建产物 HTML 作为响应返回；缺失则给出「先跑哪个脚本」的提示。"""
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.name} 还不存在，先跑 {hint}")
    return HTMLResponse(path.read_text(encoding="utf-8"))


#: 首页四别名指向同一份产物 web/index.html：
#:   `/`              对外唯一入口
#:   `/home`          旧链接兼容
#:   `/index.html`    页面间互链用相对路径（离线双击文件时只有相对路径能用），
#:                    服务模式下相对路径会解析成 /index.html，故必须也注册。
#:   `/web/index.html` 同上，供**报告页**用：报告在 reports/{期}/ 下，回首页的相对路径
#:                    是 `../../web/index.html`，服务模式下解析成 /web/index.html。
#: 四个别名收敛到同一个函数，不存在「两个首页各自漂移」的可能。
@app.get("/", response_class=HTMLResponse)
@app.get("/home", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
@app.get("/web/index.html", response_class=HTMLResponse)
def home() -> HTMLResponse:
    """首页：搜索框 + 已生成报告列表 + 横向对比入口。"""
    return _serve_html(WEB_DIR / "index.html", "python scripts/build_web_index.py")


@app.get("/watchlist", response_class=HTMLResponse)
@app.get("/watchlist.html", response_class=HTMLResponse)
def watchlist_page() -> HTMLResponse:
    """跟踪池横向对比表（/watchlist.html 是页面互链用的相对路径落点）。"""
    return _serve_html(WEB_DIR / "watchlist.html", "python scripts/build_watchlist.py")


@app.get("/query", response_class=HTMLResponse)
@app.get("/query.html", response_class=HTMLResponse)
def legacy_query_page() -> HTMLResponse:
    """旧版独立搜索页（web/query.html）—— 兼容访问，不再是产品入口。

    它的能力（防抖搜索 + 就地生成 + 进度轮询）已完整并入首页 web/index.html，
    所以首页上不再有指向它的按钮。这里保留路由只是不让旧书签 404。
    """
    return _serve_html(WEB_DIR / "query.html", "确认 web/query.html 是否还在仓库里")


@app.get("/report/{code}", response_class=HTMLResponse)
def view_report(code: str) -> HTMLResponse:
    """查看已生成的报告 HTML。

    代码规范化（按用户原样 → 补 5 位港股 → 补 6 位 A 股依次尝试）已在
    `artifacts.find()` 里 —— **先试原样**很重要：600036 若直接补 5 位会变成 00036
    （不符规范）甚至撞到别的标的。这里不再重复一份候选逻辑。
    """
    p = _find_report(code)
    if p:
        return HTMLResponse(p.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail=f"暂无 {code} 的报告，请先生成")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/api/health")
def health() -> dict:
    """服务状态 + **报告口径对账**。

    `reports` 是**唯一对外口径**，与首页卡片数/对比表列数同源自
    `src/report/artifacts.inventory()`。2026-09-18 之前这里算的是
    `reports/*/*.html` 的文件个数（14），而首页只列跟踪池里的（11）——
    同一个东西两个数，用户问「到底几份」。现在 `reports` == 首页显示数，
    另外两个字段给出**对不上的原因**，而不是把差异藏起来。
    """
    idx = market_index.INDEX_PATH
    inv = artifacts.inventory()
    return {
        "ok": True,
        "index": {"path": str(idx.relative_to(ROOT)), "exists": idx.exists(),
                  **market_index.stats()},
        #: 对外口径：首页卡片 / 对比表列数（池内 + 池外兜底）
        "reports": inv["shown_count"],
        "reports_detail": {
            "in_pool": len(inv["in_pool"]),        # 池内且有报告
            "orphan": len(inv["orphan"]),          # 有报告但不在池（>0 = 入池链路可能断了）
            "removed": len(inv["removed"]),        # 刻意移出，报告仍在磁盘
            "missing": len(inv["missing"]),        # 在池但还没报告
            "on_disk": len(inv["on_disk"]),        # 磁盘上全部报告文件（已排除 reports/xhs）
        },
        "orphan_codes": inv["orphan"],
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/refresh-status")
def refresh_status() -> dict:
    """数据更新证据链：回答「怎么证明数据更新过」。

    给网页用的轻量版（完整版见 `python scripts/check_refresh.py`）。
    判据优先级：文件 mtime > 归档序列 > 日志 > 闸门 —— mtime 最硬。
    """
    import pandas as pd  # noqa: PLC0415

    from src.data import watchlist_store as _wl  # noqa: PLC0415

    gate = ROOT / "data" / "logs" / ".last_success"
    gate_val = gate.read_text(encoding="utf-8").strip() if gate.exists() else None

    items = []
    for code in _wl.codes():
        qf = RAW_DIR / code / "quote.parquet"
        item = {"code": code, "name": (_wl.get(code) or {}).get("name", code)}
        if qf.exists():
            item["quote_mtime"] = datetime.fromtimestamp(qf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            try:
                df = pd.read_parquet(qf)
                if not df.empty:
                    r = df.iloc[-1]
                    item["price"] = float(r["price"]) if pd.notna(r.get("price")) else None
                    item["change_pct"] = float(r["change_pct"]) if pd.notna(r.get("change_pct")) else None
                    d = r.get("report_date")
                    item["quote_date"] = pd.Timestamp(d).strftime("%Y-%m-%d") if pd.notna(d) else None
            except Exception:
                pass
        items.append(item)

    return {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "gate": gate_val,
        "gate_is_today": gate_val == datetime.now().strftime("%Y-%m-%d"),
        "count": len(items),
        "stocks": items,
        "watchlist_rebuild": dict(_derived_state),
        "note": "quote_mtime 落在今天 = 今天的日更确实改写过行情文件；"
                "闸门有今天日期 = 整批全部成功（任一项失败都不写闸门）",
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
    """已生成的报告清单（按报告期倒序，同期内按修改时间倒序）。

    口径同 `/api/health`：走 `artifacts.scan()`，**不含** `reports/xhs/` 下的发布包
    （原先用 `glob("*/*.html")`，把九宫格发布包也列成了「报告」）。
    """
    files = sorted(artifacts.scan().values(),
                   key=lambda p: (artifacts.period_key(p.parent.name), p.stat().st_mtime),
                   reverse=True)
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


@app.get("/api/watchlist")
def watchlist_state() -> dict:
    """跟踪池当前状态 —— 供首页卡片按钮决定显示「加入对比」还是「移出对比」。

    `removed` 也一并返回：刻意移出的标的，按钮要显示成「加入」把人叫回来。
    首页兜底卡片已排除 removed（见 build_web_index.build_cards），但按钮状态要一致。
    """
    items = watchlist_store.stocks(include_removed=True)
    active = [s for s in items if s.get("status") != "removed"]
    return {
        "count": len(active),
        # 2026-09-18 去掉 `max_size`：跟踪池不再有名义上限（见 watchlist_store 模块 docstring），
        # 响应里留一个永远是 8 的字段只会让前端再算一次「超没超」。
        "items": [
            {"code": s["bare"], "name": s.get("name", ""),
             "industry": s.get("industry", ""), "lynch": s.get("lynch", ""),
             "lynch_note": s.get("lynch_note", ""),
             "color": s.get("color", ""), "status": s.get("status", "active")}
            for s in items
        ],
    }


@app.post("/api/watchlist")
def watchlist_edit(payload: dict) -> dict:
    """把标的加入 / 移出横向对比列表（= 跟踪池）。

    body: `{"code": "600519", "action": "add"|"remove", "reason": "…"}`

    返回后**异步**重建首页与对比表：重建要十几秒（逐只跑 build_template_data），
    不能让手机端干等；重建请求会被合并，连点不会重复重算。
    """
    code = str(payload.get("code", "")).strip()
    action = str(payload.get("action", "")).strip().lower()
    if not code:
        raise HTTPException(status_code=400, detail="缺少 code 参数")
    if action not in ("add", "remove"):
        raise HTTPException(status_code=400, detail="action 只能是 add 或 remove")

    bare = watchlist_store.bare(code)
    if action == "add":
        # 已移出的走 restore（恢复原条目，不追加第二条）；从没入过池的才 add
        if watchlist_store.get(bare, include_removed=True) is None:
            watchlist_store.add(bare, source="manual")
        else:
            watchlist_store.restore(bare)
        state = "active"
    else:
        out = watchlist_store.remove(bare, reason=str(payload.get("reason", "") or ""))
        if out is None and watchlist_store.get(bare, include_removed=True) is None:
            raise HTTPException(status_code=404, detail=f"{code} 不在对比列表里，无需移出")
        state = "removed"

    s = watchlist_store.get(bare, include_removed=True) or {}
    return {
        "ok": True, "code": bare, "action": action, "state": state,
        "name": s.get("name", ""), "industry": s.get("industry", ""),
        "lynch": s.get("lynch", ""), "lynch_note": s.get("lynch_note", ""),
        "color": s.get("color", ""),
        "count": len(watchlist_store.codes()),
        "rebuild_queued": _schedule_derived_rebuild(),
    }


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


# 🔴 必须放在所有 @app.get 之后：mount 也是往 app.routes 里追加，注册在前会抢占匹配。
# 报告原文（reports/{期}/{code}.html + 同名 png）—— 首页卡片链接是相对路径
# `../reports/2026Q2/601088.html`，服务模式下解析成 /reports/...，靠这个挂载提供。
# check_dir=False：首次运行时 reports/ 可能还不存在，不该因此起不来。
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR), check_dir=False), name="reports")


if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="fqf 一页报告服务")
    ap.add_argument("--host", default="127.0.0.1", help="绑定地址（手机访问用 0.0.0.0）")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("web.server:app", host=a.host, port=a.port, reload=a.reload)
