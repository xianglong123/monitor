#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data/ 里的每日差异渲染成 site/index.html（纯静态，无 JS 依赖）。"""

import html
import json
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SITE_DIR = os.path.join(ROOT, "site")
CST = timezone(timedelta(hours=8))
SHOW_DAYS = 30

SOURCE_META = {
    "openrouter": {"label": "模型与定价", "hint": "在售模型数量、价格、上下文长度", "color": "#1f6feb"},
    "mcp": {"label": "MCP 服务器", "hint": "官方注册表里新出现的工具", "color": "#8250df"},
    "hf": {"label": "热门论文", "hint": "Hugging Face 每日精选", "color": "#1a7f37"},
    "arxiv": {"label": "AI 论文", "hint": "cs.AI / cs.CL 最新提交", "color": "#1a7f37"},
    "hn": {"label": "社区热帖", "hint": "Hacker News 上的 AI 相关讨论", "color": "#bc4c00"},
}
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def enabled_sources():
    """按 sources.json 的开关决定页面上出现哪些源，关掉的源连卡片都不显示。"""
    cfg = load_json(os.path.join(ROOT, "sources.json"), {})
    out = []
    for s in cfg.get("sources", []) or []:
        if s.get("enabled", True) and s.get("id") in SOURCE_META:
            out.append(s["id"])
    return out or list(SOURCE_META)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def esc(s):
    return html.escape(str(s if s is not None else ""))


def money(v):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return "$%g/M" % v
    return esc(v)


def weekday_of(date_str):
    try:
        return WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except Exception:
        return ""


def meta_brief(sid, item):
    """每条只给一行最有信息量的补充，不堆字段。"""
    m = item.get("meta") or {}
    if sid == "openrouter":
        bits = []
        if m.get("prompt") is not None:
            bits.append("输入 " + money(m.get("prompt")))
        if m.get("completion") is not None:
            bits.append("输出 " + money(m.get("completion")))
        if m.get("ctx"):
            bits.append("%sK 上下文" % round(m["ctx"] / 1000))
        return " · ".join(bits)
    if sid == "mcp":
        desc = (m.get("desc") or "").strip()
        return esc(desc[:110])
    if sid == "arxiv":
        return esc(" / ".join(m.get("cats") or [])[:60])
    if sid == "hf":
        bits = []
        if m.get("up") is not None:
            bits.append("%s 赞" % m["up"])
        if m.get("pub"):
            bits.append(m["pub"])
        return " · ".join(bits)
    if sid == "hn":
        bits = []
        if m.get("score") is not None:
            bits.append("%s 分" % m["score"])
        if m.get("comments") is not None:
            bits.append("%s 评论" % m["comments"])
        return " · ".join(bits)
    return ""


def render_change(sid, ch):
    """价格/上下文变化：把 from → to 里真正变的字段挑出来。"""
    f, t = ch.get("from") or {}, ch.get("to") or {}
    bits = []
    if f.get("prompt") != t.get("prompt"):
        bits.append("输入 %s → %s" % (money(f.get("prompt")), money(t.get("prompt"))))
    if f.get("completion") != t.get("completion"):
        bits.append("输出 %s → %s" % (money(f.get("completion")), money(t.get("completion"))))
    if f.get("ctx") != t.get("ctx"):
        old = round(f["ctx"] / 1000) if f.get("ctx") else "—"
        new = round(t["ctx"] / 1000) if t.get("ctx") else "—"
        bits.append("上下文 %sK → %sK" % (old, new))
    if not bits:
        bits.append("元数据变化")
    return " · ".join(bits)


def render_day(day):
    date = day.get("date", "")
    counts = day.get("counts") or {}
    srcs = day.get("sources") or {}
    new_total = sum((c.get("new") or 0) for c in counts.values())
    changed_total = sum((c.get("changed") or 0) for c in counts.values())
    gone_total = sum((c.get("gone") or 0) for c in counts.values())

    parts = []
    parts.append('<section class="day">')
    parts.append('<div class="day-head"><div class="day-date">%s <span>%s</span></div>' % (esc(date), weekday_of(date)))
    parts.append('<div class="day-nums">')
    if day.get("baseline"):
        parts.append('<span class="n-base">基线建立</span>')
    else:
        parts.append('<span class="n-new">+%d 新增</span>' % new_total)
        if changed_total:
            parts.append('<span class="n-chg">~%d 变化</span>' % changed_total)
        if gone_total:
            parts.append('<span class="n-gone">-%d 下架</span>' % gone_total)
    parts.append("</div></div>")

    if day.get("baseline"):
        parts.append(
            '<div class="base-note">首次运行只建立基线，不把已有条目算成新增。明天的抓取才有真正的对比。</div>'
        )

    for sid in enabled_sources():
        block = srcs.get(sid) or {}
        meta = SOURCE_META.get(sid, {"label": sid, "hint": "", "color": "#57606a"})
        err = block.get("error")
        rows = []

        if block.get("baseline"):
            label = "首次接入 %d 条" % block["baseline"] if block.get("first") else "基线 %d 条" % block["baseline"]
            note = "从明天开始记变化" if block.get("first") else "今天不记为新增"
            rows.append('<li class="r base"><i>=</i><span>%s</span><em>%s</em></li>' % (esc(label), esc(note)))

        for it in (block.get("new") or [])[:60]:
            brief = meta_brief(sid, it)
            rows.append(
                '<li class="r new"><i>+</i><a href="%s" target="_blank" rel="noopener">%s</a><em>%s</em></li>'
                % (esc(it.get("url", "")), esc(it.get("title", "")), brief)
            )
        for ch in (block.get("changed") or [])[:40]:
            rows.append(
                '<li class="r chg"><i>~</i><a href="%s" target="_blank" rel="noopener">%s</a><em>%s</em></li>'
                % (esc(ch.get("url", "")), esc(ch.get("title", "")), esc(render_change(sid, ch)))
            )
        for it in (block.get("gone") or [])[:30]:
            rows.append(
                '<li class="r gone"><i>−</i><span>%s</span><em>已下架</em></li>'
                % esc(it.get("title", ""))
            )

        if err:
            rows.append('<li class="r err"><i>!</i><span>这个源今天没抓到</span><em>%s</em></li>' % esc(err[:160]))

        if not rows:
            continue

        c = counts.get(sid) or {}
        head_num = c.get("new") or 0
        parts.append('<div class="src" style="--c:%s">' % meta["color"])
        parts.append(
            '<div class="src-head"><b>%s</b><span>%s</span><em>+%d / 共 %s</em></div>'
            % (esc(meta["label"]), esc(meta["hint"]), head_num, c.get("total", "—"))
        )
        parts.append("<ul>" + "".join(rows) + "</ul></div>")

    parts.append("</section>")
    return "".join(parts)


def build_page(days, index):
    latest = days[0] if days else None
    total_new_today = sum((c.get("new") or 0) for c in (latest.get("counts") or {}).values()) if latest else 0

    cards = []
    counts = (latest.get("counts") or {}) if latest else {}
    srcs = (latest.get("sources") or {}) if latest else {}
    for sid in enabled_sources():
        meta = SOURCE_META[sid]
        c = counts.get(sid) or {}
        err = (srcs.get(sid) or {}).get("error")
        if err and not c.get("total"):
            cards.append(
                '<div class="card is-off" style="--c:%s"><div class="card-top">%s</div>'
                '<div class="card-num">—</div>'
                '<div class="card-sub">今天没抓到 · 明天再试</div></div>'
                % (meta["color"], esc(meta["label"]))
            )
            continue
        n = c.get("new") or 0
        cards.append(
            '<div class="card" style="--c:%s"><div class="card-top">%s</div>'
            '<div class="card-num">%s</div>'
            '<div class="card-sub">共 %s 条 · 今日变化 %s</div></div>'
            % (meta["color"], esc(meta["label"]), ("+%d" % n) if n else "0", c.get("total", "—"), c.get("changed") or 0)
        )

    days_html = "".join(render_day(d) for d in days)

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 供给侧变化</title>
<meta name="description" content="每天只记录变化：模型与定价、MCP 工具、AI 论文、社区热帖。由 GitHub Actions 自动抓取与发布。">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <h1>AI 供给侧变化</h1>
  <p class="sub">模型 · 工具 · 论文 · 社区，每天只记「和昨天不同的部分」</p>
  <p class="stamp">最近一次抓取 <b>%(stamp)s</b> · 今日新增 <b>%(total)s</b> 条</p>
</header>

<div class="cards">%(cards)s</div>

<main>%(days)s</main>

<footer>
  <p><b>口径</b>：只存差异，不存全量。state.json 记住「上次看到的样子」，每天对比后写出当日变化，因此仓库体积不随时日膨胀。</p>
  <p><b>数据源</b>：OpenRouter 模型接口 · Model Context Protocol 官方注册表 · arXiv cs.AI/cs.CL · Hacker News。全部为公开接口，无任何私有数据。</p>
  <p><b>为什么</b>：这份连续的变化序列本身就是资产——页面可以被抄走，三个月的 diff 不能。</p>
  <p class="sig">自动生成 · <a href="https://github.com/xianglong123/monitor">源码</a></p>
</footer>
</body>
</html>
""" % {
        "stamp": esc((latest or {}).get("generated_at", "")[:16].replace("T", " ")),
        "total": total_new_today,
        "cards": "".join(cards),
        "days": days_html,
    }


def main():
    index = load_json(os.path.join(DATA_DIR, "index.json"), [])
    days = []
    for item in index[:SHOW_DAYS]:
        d = load_json(os.path.join(DATA_DIR, item.get("date", "") + ".json"), None)
        if d:
            days.append(d)
    if not days:
        print("没有可渲染的数据，先跑 collect.py")
        return 1

    page = build_page(days, index)
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print("已生成 site/index.html（%d 天，%d 字节）" % (len(days), len(page)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
