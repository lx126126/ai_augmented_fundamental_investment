"""巨潮资讯网：年报公告查询 + PDF 下载。

数据验证的「金标准」来源：巨潮披露的官方年报 PDF（文本版）。
流程：查公告拿 orgId → hisAnnouncement/query 拿 adjunctUrl → 下载 PDF。
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

# 环境变量可能有 Veee 代理残留（15236），国内站点必须禁用代理直连
_PROXIES = {"http": None, "https": None}
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "http://www.cninfo.com.cn/",
}
_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_PDF_BASE = "http://static.cninfo.com.cn"

# 定期报告公告类别：年报 / 半年报 / 一季报 / 三季报
CATEGORY_ANNUAL = "category_ndbg_szsh;"
CATEGORY_SEMI = "category_bndbg_szsh;"
CATEGORY_Q1 = "category_yjdbg_szsh;"
CATEGORY_Q3 = "category_sjdbg_szsh;"


_ORG_CACHE_DIR = Path("data/cache/cninfo_org")
_TOP_SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"


def _get_org_id(code: str) -> str:
    """解析巨潮 orgId：本地缓存 → 巨潮 topSearch → akshare 兜底。

    原实现只用 akshare 的 `stock_zh_a_disclosure_report_cninfo`，那个接口会整表拉公告，
    既重又会偶发返回非 JSON（实测 `RequestsJSONDecodeError: Expecting value`），
    一旦它抖动，整条「定期报告 → 管理层讨论 → 季度解读」链路跟着全废，且报错信息
    完全指向不到真正原因。topSearch 是官方轻量入口，只为拿 orgId 时不该走重接口。
    """
    cache = _ORG_CACHE_DIR / f"{code}.txt"
    if cache.exists():
        v = cache.read_text(encoding="utf-8").strip()
        if v:
            return v

    # 1) 巨潮 topSearch（轻量、官方）
    try:
        r = requests.post(
            _TOP_SEARCH_URL,
            data={"keyWord": code, "maxNum": "10"},
            headers=_HEADERS, timeout=20, proxies=_PROXIES,
        )
        r.raise_for_status()
        for item in (r.json() or []):
            if str(item.get("code")) == code and item.get("orgId"):
                org = str(item["orgId"])
                _ORG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(org, encoding="utf-8")
                return org
    except Exception:
        pass

    # 2) akshare 兜底（原实现，从公告链接里正则提取）
    import akshare as ak

    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol=code, market="沪深京", keyword="", category="年报",
        start_date="20100101", end_date="20991231",
    )
    if df is None or df.empty:
        raise RuntimeError(f"未查到 {code} 的公告，无法获取 orgId")
    link = str(df.iloc[0]["公告链接"])
    # orgId 格式不一：早期上市公司为纯数字（9900003701），部分为市场前缀+代码（gssh0600519/gssz0000651）
    m = re.search(r"orgId=([^&]+)", link)
    if not m:
        raise RuntimeError(f"公告链接中未找到 orgId: {link}")
    org = m.group(1)
    try:
        _ORG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(org, encoding="utf-8")
    except Exception:
        pass
    return org


def query_annual_reports(code: str) -> list[dict]:
    """查询公司全部年报公告，返回 [{title, year, announcement_id, pdf_url}]（最新在前）。"""
    org_id = _get_org_id(code)
    body = {
        "pageNum": "1", "pageSize": "30", "column": "szse",
        "tabName": "fulltext", "plate": "", "stock": f"{code},{org_id}",
        "searchkey": "", "secid": "", "category": CATEGORY_ANNUAL,
        "trade": "", "seDate": "2010-01-01~2099-12-31",
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    r = requests.post(_QUERY_URL, data=body, headers=_HEADERS, timeout=30, proxies=_PROXIES)
    r.raise_for_status()
    anns = r.json().get("announcements") or []

    reports = []
    for a in anns:
        title = a.get("announcementTitle", "")
        # 只要中文版年报正文，排除「摘要」「英文版」
        if "摘要" in title or "年度报告" not in title:
            continue
        if "英文" in title:
            continue
        adjunct = a.get("adjunctUrl") or ""
        pdf_url = f"{_PDF_BASE}/{adjunct}" if adjunct else ""
        year = _extract_year(title)
        reports.append({
            "title": title,
            "year": year,
            "announcement_id": a.get("announcementId"),
            "pdf_url": pdf_url,
        })
    return reports


def _extract_year(title: str) -> int | None:
    """从公告标题提取年份，兼容「中国神华2025年度报告」和「2019年年度报告」两种格式。"""
    m = re.search(r"(20\d{2})", title)
    return int(m.group(1)) if m else None


# 定期报告四类的「标题特征 → 类别键」。季度解读要的是「最新一期」，四类都要查，
# 只查半年报会在三季报已披露时抓到过期数据。
_PERIODIC_KINDS = (
    (CATEGORY_SEMI, "半年报", ("半年度报告", "中期报告")),
    (CATEGORY_Q3, "三季报", ("第三季度报告", "三季度报告")),
    (CATEGORY_Q1, "一季报", ("第一季度报告", "一季度报告")),
    (CATEGORY_ANNUAL, "年报", ("年度报告",)),
)


def query_periodic_reports(code: str) -> list[dict]:
    """查询公司全部定期报告（年报/半年报/一季报/三季报），按披露时间倒序。

    返回 [{title, kind, year, date, pdf_url, announcement_id}]。
    只保留中文正文，排除摘要/英文版/更正公告（更正公告正文是「经调整后」的重复披露，
    拿它当底稿会让 LLM 把同一件事说两遍）。
    """
    org_id = _get_org_id(code)
    out: list[dict] = []
    for category, kind, keywords in _PERIODIC_KINDS:
        body = {
            "pageNum": "1", "pageSize": "30", "column": "szse",
            "tabName": "fulltext", "plate": "", "stock": f"{code},{org_id}",
            "searchkey": "", "secid": "", "category": category,
            "trade": "", "seDate": "2010-01-01~2099-12-31",
            "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        try:
            r = requests.post(_QUERY_URL, data=body, headers=_HEADERS, timeout=30, proxies=_PROXIES)
            r.raise_for_status()
            anns = r.json().get("announcements") or []
        except Exception:
            continue
        for a in anns:
            title = a.get("announcementTitle", "") or ""
            if "摘要" in title or "英文" in title or "更正" in title:
                continue
            if not any(k in title for k in keywords):
                continue
            adjunct = a.get("adjunctUrl") or ""
            if not adjunct:
                continue
            out.append({
                "title": title,
                "kind": kind,
                "year": _extract_year(title),
                "date": a.get("announcementTime"),
                "pdf_url": f"{_PDF_BASE}/{adjunct}",
                "announcement_id": a.get("announcementId"),
            })
    out.sort(key=lambda x: (x.get("date") or 0), reverse=True)
    return out


def download_report_pdf(code: str, report: dict, out_dir: Path) -> Path:
    """下载指定定期报告 PDF，返回文件路径（已存在则直接返回）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    year = report.get("year") or "unknown"
    target = out_dir / f"{code}_{year}{report.get('kind', '定期报告')}.pdf"
    if target.exists() and target.stat().st_size > 1000:
        return target
    r = requests.get(report["pdf_url"], headers=_HEADERS, timeout=120, proxies=_PROXIES)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"下载的不是 PDF: {report['pdf_url']}")
    target.write_bytes(r.content)
    return target


def download_annual_report(code: str, year: int, out_dir: Path) -> Path:
    """下载指定年份年报 PDF，返回文件路径（已存在则直接返回）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{code}_{year}年报.pdf"
    if target.exists():
        return target

    reports = query_annual_reports(code)
    hit = next((x for x in reports if x["year"] == year), None)
    if not hit or not hit["pdf_url"]:
        raise RuntimeError(f"未找到 {code} {year} 年报 PDF 链接")

    r = requests.get(hit["pdf_url"], headers=_HEADERS, timeout=60, proxies=_PROXIES)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"下载的不是 PDF: {hit['pdf_url']}")
    target.write_bytes(r.content)
    return target
