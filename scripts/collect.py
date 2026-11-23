#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每天抓一次公开数据源，只算「和昨天比变了什么」，把结果写进 data/。

设计取舍：
1. 只依赖 Python 标准库 —— Actions 里不用装任何包，少一个失败点。
2. 单个源挂掉不影响整体：失败会记进当日文件，页面照常出，其余源照常抓。
3. state.json 存「上次看到的样子」，当日文件只存差异。这样仓库体积是常数级增长。
"""

import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
UA = "ai-supply-monitor/1.0 (+https://xianglong123.github.io/monitor/; daily diff bot)"
CST = timezone(timedelta(hours=8))
KEEP_INDEX_DAYS = 400


def log(msg):
    print(msg, flush=True)


def _open(url, headers=None, timeout=30):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)


def get_json(url, headers=None, timeout=30, retries=2):
    for attempt in range(retries + 1):
        try:
            with _open(url, headers, timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if attempt == retries:
                raise
            log("  重试 %s（%s）" % (url.split("?")[0], e))
            time.sleep(2)


def get_text(url, timeout=40, retries=2):
    for attempt in range(retries + 1):
        try:
            with _open(url, None, timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if attempt == retries:
                raise
            log("  重试 %s（%s）" % (url.split("?")[0], e))
            time.sleep(2)


# ---------------------------------------------------------------- 抓取器

def fetch_openrouter():
    """在售模型 + 定价 + 上下文长度。价格单位是「每 token 美元」，这里统一换成每百万。"""
    d = get_json("https://openrouter.ai/api/v1/models")
    items = {}
    for m in d.get("data", []):
        mid = m.get("id")
        if not mid:
            continue
        p = m.get("pricing") or {}

        def per_million(v):
            try:
                return round(float(v) * 1_000_000, 4)
            except (TypeError, ValueError):
                return None

        items[mid] = {
            "title": m.get("name") or mid,
            "url": "https://openrouter.ai/models/" + mid.split(":")[0],
            "meta": {
                "prompt": per_million(p.get("prompt")),
                "completion": per_million(p.get("completion")),
                "ctx": m.get("context_length"),
            },
        }
    return items


def fetch_mcp():
    """官方 MCP 注册表，抓 3 页共 300 条。按名字去重。"""
    items = {}
    cursor = None
    for page in range(3):
        url = "https://registry.modelcontextprotocol.io/v0/servers?limit=100"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(str(cursor))
        d = get_json(url)
        for s in d.get("servers") or []:
            srv = s.get("server") or s
            name = srv.get("name")
            if not name or name in items:
                continue
            items[name] = {
                "title": srv.get("title") or name,
                "url": (srv.get("repository") or {}).get("url")
                or "https://registry.modelcontextprotocol.io/v0/servers?search=" + urllib.parse.quote(name),
                "meta": {"desc": (srv.get("description") or "")[:160], "version": srv.get("version")},
            }
        cursor = (d.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break
        time.sleep(1)
    return items


ARXIV_NS = {"a": "http://www.w3.org/2005/Atom"}


def fetch_arxiv():
    """cs.AI / cs.CL 最新提交，60 条。

    arXiv 的 API 限流很硬（短时间多次请求直接 429），所以这里单独做长退避，
    而不是复用 get_text 的短重试。
    """
    url = (
        "https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.CL"
        "&start=0&max_results=60&sortBy=submittedDate&sortOrder=descending"
    )
    xml = None
    for attempt in range(3):
        try:
            xml = get_text(url, timeout=45, retries=0)
            break
        except Exception as e:
            if attempt == 2:
                raise
            wait = 10 * (attempt + 1)
            log("  arXiv 被限流（%s），等 %ds 后重试" % (e, wait))
            time.sleep(wait)
    items = {}
    root = ET.fromstring(xml)
    for e in root.findall("a:entry", ARXIV_NS):
        aid = (e.findtext("a:id", "", ARXIV_NS) or "").strip()
        if not aid:
            continue
        key = aid.split("/abs/")[-1].split("v")[0]
        title = " ".join((e.findtext("a:title", "", ARXIV_NS) or "").split())
        cats = [c.get("term") for c in e.findall("a:category", ARXIV_NS) if c.get("term")]
        items[key] = {
            "title": title,
            "url": "https://arxiv.org/abs/" + key,
            "meta": {"cats": cats[:3], "primary": cats[0] if cats else ""},
        }
    return items


AI_KEYWORDS = (
    " ai ", "ai-", " ai,", "llm", "gpt", "claude", "anthropic", "openai", "gemini",
    "llama", "agent", "mcp", "rag", "diffusion", "transformer", "embedding",
    "fine-tun", "inference", "token", "prompt", "chatbot", "copilot", "vibe",
)
AI_TITLE_RE = None


def _is_ai(title):
    t = " " + (title or "").lower() + " "
    if t.startswith(" ai ") or " ai " in t:
        return True
    return any(k in t for k in AI_KEYWORDS)


def fetch_hn():
    """HN 前 45 条里，标题命中 AI 关键词的。并发取详情，避免串行超时。"""
    ids = get_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:45]

    def one(i):
        try:
            it = get_json("https://hacker-news.firebaseio.com/v0/item/%d.json" % i, timeout=10, retries=0)
        except Exception:
            return None
        if not it or it.get("type") != "story":
            return None
        title = it.get("title") or ""
        if not _is_ai(title):
            return None
        key = str(it["id"])
        return key, {
            "title": title,
            "url": it.get("url") or "https://news.ycombinator.com/item?id=" + key,
            "meta": {"score": it.get("score"), "comments": it.get("descendants")},
        }

    items = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for r in pool.map(one, ids):
            if r:
                items[r[0]] = r[1]
    return items


def fetch_hf():
    """Hugging Face 每日精选论文。

    arXiv 的 API 对机房 IP 限流极狠（本地和 GitHub Actions 上都稳定 429），
    所以换成这个源：走 Cloudflare，稳定性好；内容又是「当天社区在看的论文」，
    经过投票筛选，信噪比比 arXiv 全量新提交更高。
    """
    data = json.loads(get_text("https://huggingface.co/api/daily_papers?limit=40", timeout=30))
    items = {}
    for it in data or []:
        paper = it.get("paper") or {}
        pid = (paper.get("id") or it.get("slug") or "").strip()
        if not pid:
            continue
        title = " ".join((it.get("title") or paper.get("title") or pid).split())
        items[pid] = {
            "title": title,
            "url": "https://huggingface.co/papers/" + pid,
            "meta": {
                "up": it.get("upvotes"),
                "cm": it.get("numComments"),
                "pub": (paper.get("publishedAt") or "")[:10],
            },
        }
    return items


FETCHERS = {
    "openrouter": fetch_openrouter,
    "mcp": fetch_mcp,
    "hf": fetch_hf,
    "arxiv": fetch_arxiv,
    "hn": fetch_hn,
}

# 这些字段只用于展示，不参与「变化」判定。
# HN 的分数和评论数每几分钟就涨一次，算进 diff 的话每天都会刷出一堆没有信息量的「变化」。
IGNORE_IN_COMPARE = {
    "hn": ("score", "comments"),
}


def cmp_meta(sid, cur_meta, prev_meta):
    """比较两条记录的元数据，跳过该源配置的噪声字段。"""
    skip = IGNORE_IN_COMPARE.get(sid, ())
    a = {k: v for k, v in (cur_meta or {}).items() if k not in skip}
    b = {k: v for k, v in (prev_meta or {}).items() if k not in skip}
    return a != b


# ---------------------------------------------------------------- 主流程

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")


def main():
    cfg = load_json(os.path.join(ROOT, "sources.json"), {"sources": []})
    state_path = os.path.join(DATA_DIR, "state.json")
    prev_state = load_json(state_path, {"sources": {}}).get("sources", {})
    # 第一次运行没有基线，全部条目都会算成「新增」，页面会是一屏噪音。
    # 所以首跑只建立基线、不产出差异。
    is_baseline = not os.path.exists(state_path)

    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    day = {"date": today, "generated_at": now.isoformat(timespec="seconds"), "baseline": is_baseline, "sources": {}, "counts": {}}
    next_state = {}
    total_new = 0

    for s in cfg.get("sources", []):
        sid = s["id"]
        if not s.get("enabled", True):
            continue
        log("[%s] 抓取中…" % sid)
        prev = prev_state.get(sid, {}) or {}
        try:
            cur = FETCHERS[sid]()
            log("  取到 %d 条（上次 %d 条）" % (len(cur), len(prev)))
        except Exception as e:
            log("  失败：%s" % e)
            day["sources"][sid] = {"error": str(e)[:300], "new": [], "gone": [], "changed": []}
            day["counts"][sid] = {"new": 0, "changed": 0, "gone": 0, "total": len(prev)}
            next_state[sid] = prev
            continue

        # 首跑建基线；某个源第一次成功接入时同理，否则会一次性刷出一屏「新增」
        first_for_source = (not prev) and not is_baseline
        if is_baseline or first_for_source:
            day["sources"][sid] = {
                "new": [],
                "changed": [],
                "gone": [],
                "baseline": len(cur),
                "first": first_for_source,
            }
            day["counts"][sid] = {"new": 0, "changed": 0, "gone": 0, "total": len(cur)}
            keep = s.get("keep") or 0
            next_state[sid] = dict(list(cur.items())[:keep]) if keep and len(cur) > keep else cur
            continue

        new_ids = [i for i in cur if i not in prev]
        changed_ids = [i for i in cur if i in prev and cmp_meta(sid, cur[i].get("meta"), prev[i].get("meta"))]
        gone_ids = [i for i in prev if i not in cur] if s.get("report_gone") else []

        day["sources"][sid] = {
            "new": [{"id": i, "title": cur[i]["title"], "url": cur[i].get("url", ""), "meta": cur[i].get("meta")} for i in new_ids],
            "changed": [
                {"id": i, "title": cur[i]["title"], "url": cur[i].get("url", ""), "from": prev[i].get("meta"), "to": cur[i].get("meta")}
                for i in changed_ids
            ],
            "gone": [{"id": i, "title": prev[i].get("title", i), "url": prev[i].get("url", "")} for i in gone_ids],
        }
        day["counts"][sid] = {
            "new": len(new_ids),
            "changed": len(changed_ids),
            "gone": len(gone_ids),
            "total": len(cur),
        }
        total_new += len(new_ids)

        keep = s.get("keep") or 0
        if keep and len(cur) > keep:
            cur = dict(list(cur.items())[:keep])
        next_state[sid] = cur

    dump_json(state_path, {"updated": now.isoformat(timespec="seconds"), "sources": next_state})
    dump_json(os.path.join(DATA_DIR, today + ".json"), day)

    # 索引：日期列表 + 每天各源计数，渲染页面只用这个 + 当日明细
    index_path = os.path.join(DATA_DIR, "index.json")
    index = load_json(index_path, [])
    summary = {
        "date": today,
        "baseline": day.get("baseline", False),
        "counts": {k: {kk: vv for kk, vv in v.items()} for k, v in day["counts"].items()},
        "errors": [k for k, v in day["sources"].items() if v.get("error")],
    }
    index = [i for i in index if i.get("date") != today]
    index.append(summary)
    index.sort(key=lambda x: x.get("date", ""), reverse=True)
    dump_json(index_path, index[:KEEP_INDEX_DAYS])

    log("完成：%s，新增 %d 条，写入 data/%s.json" % (today, total_new, today))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
