# 数据地图 · 数据在哪、谁在跑、怎么更新

> 本文回答三个最容易搞混的问题：**数据到底存在哪里**、**什么在触发它更新**、**更新时是覆盖还是追加**。
> 所有数字均为 **2026-09-17 在本机（MacBookAir7,2 / 8GB / macOS 12.7.6）实测**。
>
> 相关文档：[full-market.md](full-market.md)（全市场链路细节）、[data-validation.md](data-validation.md)（四层校验体系）。

---

## 1. 数据在哪里

**没有数据库服务器。** 所有数据都是 `data/` 目录下的文件，共四处：

| 位置 | 内容 | 格式 | 覆盖范围 | 体积 |
|---|---|---|---|---|
| `data/raw/{code}/` | 财报 9 张表（利润表/资产负债表/现金流量表/财务指标/分红/分业务/估值/行情/评级/竞争/概况） | Parquet，**每标的每表一个文件** | **11 只**（观察池 6 + 中国海油 A/H + 历史查询产物） | 1.9 MB |
| `data/market/` | `index.parquet`（全市场标的索引）+ `spot_latest.parquet`（当日全市场行情）+ 6 只旧标的的 quote/valuation | Parquet | **全市场 8368 只** | 1.2 MB |
| `data/warehouse/fqf.duckdb` | `raw` 层 11 张表 + `mart` 层 4 张表 | **单个 DuckDB 文件** | **混合**：`mart.market_index` 全量，其余 raw/mart 仅 8 只 | 12.3 MB |
| `data/cache/` + `data/validation/` | LLM 叙事缓存（按事实数据 md5 键）+ 年报 PDF 金标准 | JSON | — | 87 MB + 77 MB |

现存的 11 只标的目录：
`000651` 格力　`00700` 腾讯　`00883` 中国海油 H　`09992` 泡泡玛特　`300061` 旗天科技　`600036` 招商银行
`600519` 茅台　`600887` 伊利　`600938` 中国海油 A　`601088` 神华　`601328` 交行

### 1.1 DuckDB 是「文件」不是「服务器」（关键概念）

这点最容易误解，展开说：

| 你以为的数据库 | DuckDB 的实际形态 |
|---|---|
| 需要启动服务、监听端口 | **没有任何常驻进程** |
| 需要账号密码、连接串 | 无需认证，`duckdb.connect("路径")` 直接开 |
| 数据在服务端 | 数据就是**磁盘上那一个文件**，拷走文件 = 拷走整个库 |
| 需要单独备份 | 复制 `fqf.duckdb` 即可（但文件正被写时不要拷） |

所以「数据库在哪」的答案是：**在 `data/warehouse/fqf.duckdb` 这个文件里**。
推论：所谓「备份数据库」到这里就是「复制一个文件」，不需要 dump / restore 那一套；
「迁移数据库」就是把这个文件拷到另一台机器。

### 1.2 `fqf.duckdb` 里实际有什么（实测行数）

| schema | 表 | 行数 | 说明 |
|---|---|---|---|
| `mart` | `market_index` | **8368** | 全市场标的索引（唯一全量的 mart 表） |
| `mart` | `annual_financials` | 177 | 年度财务宽表 |
| `mart` | `quarter_financials` | 64 | 季度财务宽表 |
| `mart` | `segments` | 111 | 分业务汇总 |
| `raw` | `valuation` | 14,081 | 百度逐日估值长表 |
| `raw` | `segments` | 926 | 分业务条线 |
| `raw` | `profit_sheet` | 586 | 利润表 |
| `raw` | `balance_sheet` | 572 | 资产负债表 |
| `raw` | `cash_flow` | 563 | 现金流量表 |
| `raw` | `financial_indicator` | 390 | 财务指标（比率型） |
| `raw` | `dividend` | 172 | 分红 |
| `raw` | `competition` | 106 | 行业竞争地位 |
| `raw` | `quote` | 8 | 行情快照（**覆盖式单行**） |
| `raw` | `rating` | 8 | 机构评级（**覆盖式单行**） |
| `raw` | `profile` | 5 | 公司概况 |

⚠️ **`raw.*` 是物化表（BASE TABLE），不是视图** —— 它只在跑 `python -m src.data.warehouse` 时重建，
因此 **DuckDB 里的数据会落后于 `data/raw/` 的 parquet**。要最新数据请直接读 parquet。

⚠️ `raw.competition` 的 `symbol` 列语义特殊：存的是**同业公司代码**，不是跟踪标的。
按 `count(distinct symbol)` 统计覆盖标的数会把它算成 106 只（真实是 8 只）。

---

## 2. 数据怎么更新：三层

依据一条实测结论 —— **磁盘便宜、行情便宜、财报昂贵**：

```
L0 索引层   全市场 8368 只标的（代码/名称/交易所）        秒级可查          每周构建
L1 行情层   全市场 8368 只行情快照（价/PE/PB/市值）      25.8s / 0 失败    每交易日
L2 财报层   单标的财报 + 一页报告                        拉数 63.5s + 生成约 3 分钟   按需 / 每季度
```

成本依据：单只 A 股冷启动 `fetch_all` **63.5s**（5565 只串行 ≈ **98 小时**）；
单只港股 `fetch_all_hk` **4.6s**（2803 只 ≈ 3.6 小时）；单只 raw 约 150KB（全量约 1.2GB）。
**所以「跑通全市场」= L0 + L1 全量，L2 按需。** 详见 [full-market.md](full-market.md)。

| 频率 | 动作 | 命令 |
|---|---|---|
| 每交易日收盘后 | 全市场行情快照 | `$V scripts/update_spot_all.py` |
| 每周 | 重建标的索引 | `$V scripts/build_market_index.py` |
| 每季度（财报季后） | 补全市场财报 | `$V scripts/update_financials.py --scope all --workers 4 --skip-done` |
| 随时 | 查一只股票出报告 | `$V -m uvicorn web.server:app --host 0.0.0.0 --port 8000` |

⚠️ 本机终端**没有 `python` 命令**（macOS 12.3+ 已移除该命令名，且 `/usr/bin/python3` 是 3.9.6
没装项目依赖）。先定义 `V` 再粘贴上面的命令：

```bash
V=/Users/lixiao/.workbuddy/binaries/python/envs/fqf/bin/python
```

### 🔴 覆盖式写盘 + 无版本留存

- 写盘入口只有 `src/data/storage.py:save_parquet()` → `df.to_parquet(path)`，**整文件覆写**。
- 但东财财报接口**每次返回全历史**（`000651/profit_sheet.parquet` 103 行覆盖 1998~2026），
  所以历史行不会因覆盖而丢。**例外是 `quote` / `rating`**（单行当日快照）→ 正因如此才另建
  `data/market/spot_history/` 按日追加归档。
- **真实风险**：上游财报重述（Restatement）会**静默改写旧值且不可回滚**
  —— `scripts/backup.py` 把 `data/raw` 列入「不备份」清单，理由是「可再生」，
  但重跑拿到的是**新口径**，不是当初报告发布时的**旧口径**。
  → 建议项（未实施）：`data/raw/_manifest.json` 留 hash + 抓取时间戳台账，保证报告可复现。

---

## 3. 谁在触发更新

**本机调度 = `launchd`（macOS 原生）。**

`launchd` 是 macOS 的总管家进程（PID 1），所有后台程序由它按规则拉起。
你不是「运行 launchd」，而是给它一份声明式配置 `.plist`。

| 装载位置 | 级别 | 时机 | 本项目 |
|---|---|---|---|
| `/Library/LaunchDaemons/` | 系统级 | 开机（未登录也起），root 身份 | 不用 |
| `~/Library/LaunchAgents/` | **用户级** | **登录后**起，**你的身份** | ✅ `com.fqf.daily-refresh` |

用户级必须选对：任务要读你的项目文件、用你的 venv，用 root 身份会出权限问题。

`.plist` 各字段对应关系（`scripts/com.fqf.daily-refresh.plist`）：

| 字段 | 值 | 作用 |
|---|---|---|
| `Label` | `com.fqf.daily-refresh` | 任务唯一名字 |
| `ProgramArguments` | `/bin/bash` + `scripts/daily_refresh.sh` | 跑什么 |
| `StartCalendarInterval` | 周一~周五 16:30 | 何时跑（16:30 兼顾 A 股 15:00 与港股 16:00 收盘） |
| `RunAtLoad` | `true` | **开机/登录后补跑**跳过的任务 |
| `StandardOutPath` / `StandardErrorPath` | `data/logs/launchd.*.log` | 日志落哪 |
| `KeepAlive` | `false` | 批处理任务，失败不无限重启 |

**与 `cron` 的决定性差别**：cron 到点触发，机器睡了/关机就**整次跳过**；
launchd 配 `RunAtLoad` 能在开机后**补跑一次**（家用 Mac 常关机，这条是关键）。
脚本里用 `data/logs/.last_success` 做闸门，保证当天只跑一次。

### 🔴 当前状态：未安装

2026-09-17 核查：
- `launchctl list | grep fqf` → **空**（`com.fqf.daily-refresh` 不在系统任务表）
- `data/logs/.last_success` → **不存在**（该闸门文件只在定时任务成功跑完后写入）

→ **日更与季更两条链路目前都没有自动调度，全靠手动执行。**
这容易误判成「数据更新跑不通」，实际是**没有人按启动键**。

安装（涉及系统定时行为，执行前请确认）：

```bash
cp scripts/com.fqf.daily-refresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fqf.daily-refresh.plist
launchctl list | grep fqf                    # 确认已加载
launchctl start com.fqf.daily-refresh        # 立即手动跑一次验证
```

`--daily` 参数是必须的：叙事层缓存键是 facts-hash，而 facts 含估值与市值 ——
行情一变哈希即变、缓存失效、重新调 LLM 烧 token。`--daily` 跳过 PDF 校验与 LLM。

## 4. 速查：常见困惑

| 困惑 | 答案 |
|---|---|
| 数据库在哪？ | `data/warehouse/fqf.duckdb` 一个文件。**没有服务器** |
| 怎么连数据库？ | 不用连。`duckdb.connect("data/warehouse/fqf.duckdb")` 直接打开 |
| 为什么 `python` 命令不存在？ | macOS 12.3+ 移除了它，且系统 3.9.6 没装依赖 → 用 venv 全路径 |
| 为什么数据更新「没跑」？ | launchd 未安装，**没有任何自动调度**（§3） |
| DuckDB 里的数据是最新的吗？ | **不一定**，`raw.*` 是物化表，落后于 parquet，需重跑 warehouse |
| 报告链路读 DuckDB 吗？ | **不读**。adapter 直接读 `data/raw/**` parquet（全量时才不会 OOM） |
