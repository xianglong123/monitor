# monitor

AI 供给侧变化记录：**每天只记「和昨天不同的部分」**，不存全量。

页面：<https://xianglong123.github.io/monitor/>

## 为什么这样设计

- **只存差异**。`data/state.json` 记住「上次看到的样子」，每次跑完和它对比，只把 `new / changed / gone` 写进当日文件。仓库体积是常数级增长，不会随着时间膨胀。
- **零依赖**。只用 Python 标准库，Actions 里不用装包，少一个失败点。
- **单源失败不拖垮整体**。某个源挂了会记进当日文件（页面上显示一行灰字），其余源照常跑，workflow 不会红。
- **首跑建基线**。第一次运行或某个源首次成功接入时，不把已有条目算成「新增」，否则第一天会是一屏噪音。

## 目录

```
sources.json            要监控哪些源（开关、保留条数）
scripts/collect.py      抓取 + 计算差异
scripts/render.py       渲染 site/index.html
data/state.json         上次看到的全量快照（对比基准）
data/YYYY-MM-DD.json    当天的差异，一天一个文件
data/index.json         日期索引 + 每天各源计数
site/                   发布到 gh-pages 的静态页面
.github/workflows/daily.yml   每天 07:00（北京时间）自动跑
```

## 现在监控什么

| 源 | 内容 | 接口 |
| --- | --- | --- |
| openrouter | 在售模型、输入/输出价格、上下文长度变化 | `openrouter.ai/api/v1/models` |
| mcp | Model Context Protocol 官方注册表新增的服务器 | `registry.modelcontextprotocol.io` |
| hf | Hugging Face 每日精选论文（社区投票筛选，约 40 篇） | `huggingface.co/api/daily_papers` |
| hn | Hacker News 首页里命中 AI 关键词的帖子 | `hacker-news.firebaseio.com` |
| ~~arxiv~~ | cs.AI / cs.CL 最新提交 | 默认关闭，见下方限制 |

价格统一换算成 **每百万 token 美元**，方便横向比较。

## 加一个源

1. 在 `scripts/collect.py` 里加一个 `fetch_xxx()`，返回 `{id: {title, url, meta}}`。
2. 在 `FETCHERS` 里注册。
3. 在 `sources.json` 里加一条配置（`keep` 控制 state 里保留多少条，`report_gone` 控制是否记录下架）。
4. 在 `scripts/render.py` 的 `SOURCE_META` 里加标签和颜色。

`meta` 是参与差异比较的部分：`meta` 变了就算 `changed`。所以想追踪哪个字段，就把它放进 `meta`。

## 部署

```sh
git clone git@github.com:xianglong123/monitor.git
cd monitor
python3 scripts/collect.py   # 首次建立基线
python3 scripts/render.py
git add -A && git commit -m "init" && git push
```

然后在仓库 **Settings → Pages**：
- Source 选 **Deploy from a branch**
- Branch 选 **gh-pages** / **root**

之后每天 UTC 23:00（北京 07:00）自动抓取、提交数据、发布页面。也可以在 Actions 页面点 **Run workflow** 手动跑一次。

## 已知限制

- **arXiv 默认关闭**。它的 API 对机房 IP 限流极狠：本地和 GitHub Actions 的境外 runner 上都稳定返回 429，退避重试三次仍失败。所以换成 Hugging Face 每日精选（同样能反映「今天大家在读什么论文」，且经过社区投票筛选，信噪比更高）。想再试 arXiv：把 `sources.json` 里那条的 `enabled` 改回 `true` 即可，失败也只影响它自己。
- **MCP 注册表分页**。目前抓 3 页共 300 条，去重后约 190 条，不是全量。够看增量趋势，不适合当完整目录。
- **HN 只取首页 45 条**。它反映的是「此刻社区在聊什么」，不是全站热搜。分数和评论数每几分钟就变，所以 `IGNORE_IN_COMPARE` 里把它们排除在差异判定之外——否则每天都会刷出一堆没有信息量的「变化」。
- **时间线只显示有变化的源**。当天没变化的源不会在时间线里出现（卡片上的总量仍然全部显示），这是故意的：页面只回答「昨天到今天我们错过了什么」。
- 数据源都可能改接口。改版那天这个源会挂一天（页面会显示），改一下 `fetch_*` 即可，不影响其他源。
