# 手机看报告（伪小程序）

> 目标：把 ValueLine 一页报告变成**手机浏览器直接打开、上下滑动浏览**的体验。
> 形态上接近「伪小程序」，但不需要任何后端服务与平台审核。

## 现状

| 页面 | 文件 | 作用 |
|---|---|---|
| 跟踪池对比 | `web/watchlist.html` | 观察池标的横向对比（由 `scripts/build_watchlist.py` 生成） |
| 报告首页 | `web/index.html` | 列出跟踪池标的，点击进入单票详情（由 `scripts/build_web_index.py` 生成） |
| 查询页 | `web/query.html` | 输入代码或名称 → 按需生成一页报告（防抖搜索 + 进度轮询） |
| 单票报告 | `reports/{报告期}/{code}.html` | 已做移动端响应式适配（`@media (max-width:768px)`） |

## 怎么看：手机连同一 WiFi

只有两条路，都不需要公网。

### 方式 A：研报服务（推荐，可查任意标的）

```bash
V=/Users/lixiao/.workbuddy/binaries/python/envs/fqf/bin/python
cd /Users/lixiao/WorkBuddy/fqf
$V -m uvicorn web.server:app --host 0.0.0.0 --port 8000
```

手机浏览器打开 `http://<电脑局域网IP>:8000` —— 这个页面上有搜索框，可以**按代码或名称查任意标的**；不在 `data/raw/` 里的冷门标的会在后台按需拉数（约 3~4 分钟，页面有进度条）。

### 方式 B：静态服务器（只读，看已生成的报告）

```bash
V=/Users/lixiao/.workbuddy/binaries/python/envs/fqf/bin/python
cd /Users/lixiao/WorkBuddy/fqf
$V -m http.server 8080
# 手机浏览器打开 http://<电脑局域网IP>:8080/web/index.html
```

⚠️ 两个注意点：

- **必须用解释器全路径**。本机终端没有 `python` 命令（macOS 12.3+ 已移除该命令名，
  且 `/usr/bin/python3` 是 3.9.6，没装项目依赖）。
- **端口别撞车**：8000 给研报服务 `web/server.py`，8001 给数据查询 API `src/api/main.py`。
  静态服务器用 8080，与它们错开。

查本机局域网 IP：

```bash
ipconfig getifaddr en0
```

## 首页如何自动更新

`web/index.html` 由 `scripts/build_web_index.py` **动态生成**（扫描 `reports/` 最新报告 + `watchlist` 名称映射），不手写维护。两种情况都会重生成首页：

1. **季度报告刷新**（`scripts/build_valueline.py`）：所有票 build 完成后重生成。
2. **每日行情刷新**（`scripts/daily_refresh.py`）：估值板块轻量重刷后重生成（首页行情数字保持当日值）。

手动运行：`$V scripts/build_web_index.py`。

## 报告期路由约定

- 报告归档路径 `reports/{报告期}/{code}.html`，报告期由数据推导（如 `2026Q2`）。
- 首页链接指向最新报告期的 HTML；报告期变化时重生成首页即可。

## 后续（验证价值后再考虑）

- PWA（添加到主屏幕，接近原生 App 体验，无需审核）
