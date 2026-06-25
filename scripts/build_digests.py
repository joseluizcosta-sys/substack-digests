#!/usr/bin/env python3
"""Build a weekly Substack/newsletter digest.

Deterministic (stdlib only): fetches RSS feeds, keeps posts from the last 7 days,
writes a dated markdown digest, and updates data.json (consumed by index.html)
and the README index. No LLM, no external deps.
"""
import json
import re
import sys
import html
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
DIGESTS = ROOT / "digests"
DATA_JSON = ROOT / "data.json"
README = ROOT / "README.md"

WINDOW_DAYS = 7
UA = "Mozilla/5.0 (compatible; substack-digests/1.0; +https://github.com/joseluizcosta-sys/substack-digests)"

FEEDS = [
    {"author": "Julia de Luca — LatAm Tech Weekly", "url": "https://juliadeluca.substack.com/feed"},
    {"author": "Ben's Bites", "url": "https://www.bensbites.com/feed"},
]


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    with urlopen(req, timeout=30) as r:
        return r.read()


def strip_html(s: str) -> str:
    s = re.sub(r"(?s)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def summarize(description: str, limit: int = 220) -> str:
    text = strip_html(description or "")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def parse_feed(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        channel = root
    items = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip() or "(untitled)"
        link = (item.findtext("link") or "").strip()
        pub_raw = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or ""
        try:
            pub = parsedate_to_datetime(pub_raw)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except Exception:
            pub = None
        desc = item.findtext("description") or item.findtext(
            "{http://purl.org/rss/1.0/modules/content/}encoded"
        ) or ""
        items.append({"title": html.unescape(title), "url": link, "pub": pub, "summary": summarize(desc)})
    return items


def build_edition(now: datetime):
    cutoff = now - timedelta(days=WINDOW_DAYS)
    sections = []
    for feed in FEEDS:
        sec = {"author": feed["author"], "url": feed["url"], "posts": [], "note": None}
        try:
            items = parse_feed(fetch(feed["url"]))
            recent = [i for i in items if i["pub"] and i["pub"] >= cutoff]
            recent.sort(key=lambda i: i["pub"], reverse=True)
            for i in recent:
                sec["posts"].append({
                    "title": i["title"],
                    "url": i["url"],
                    "date": i["pub"].strftime("%b %d"),
                    "iso": i["pub"].date().isoformat(),
                    "summary": i["summary"],
                })
            if not recent:
                sec["note"] = "No new posts this week."
        except Exception as e:  # network/parse failure — record, don't crash
            sec["note"] = f"Feed unavailable ({type(e).__name__})."
        sections.append(sec)
    return {"date": now.date().isoformat(), "generated": now.isoformat(timespec="seconds"), "sections": sections}


def render_markdown(edition) -> str:
    d = datetime.fromisoformat(edition["date"])
    lines = [f"# Substack digest — week of {d.strftime('%b %d, %Y')}", ""]
    for sec in edition["sections"]:
        lines.append(f"## {sec['author']}")
        if sec["note"]:
            lines.append(f"_{sec['note']}_")
        for p in sec["posts"]:
            lines.append(f"- **[{p['title']}]({p['url']})** — {p['date']}")
            if p["summary"]:
                lines.append(f"  {p['summary']}")
        lines.append("")
    lines.append(f"_Generated {edition['generated']}_")
    return "\n".join(lines) + "\n"


def update_data_json(edition):
    data = []
    if DATA_JSON.exists():
        try:
            data = json.loads(DATA_JSON.read_text())
        except Exception:
            data = []
    data = [e for e in data if e.get("date") != edition["date"]]
    data.append(edition)
    data.sort(key=lambda e: e["date"], reverse=True)
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


def update_readme(data):
    head = "# substack-digests\n\nWeekly digests of selected newsletters, built automatically every Monday by a GitHub Action.\n\n**Viewer:** https://joseluizcosta-sys.github.io/substack-digests/\n\n## Editions\n\n"
    lines = [f"- [{e['date']}](digests/substack-{e['date']}.md)" for e in data]
    README.write_text(head + "\n".join(lines) + "\n")


def main():
    now = datetime.now(timezone.utc)
    edition = build_edition(now)
    DIGESTS.mkdir(exist_ok=True)
    (DIGESTS / f"substack-{edition['date']}.md").write_text(render_markdown(edition))
    data = update_data_json(edition)
    update_readme(data)
    total = sum(len(s["posts"]) for s in edition["sections"])
    print(f"Edition {edition['date']}: {total} posts across {len(edition['sections'])} feeds.")
    for s in edition["sections"]:
        print(f"  - {s['author']}: {len(s['posts'])} posts" + (f" ({s['note']})" if s['note'] else ""))


if __name__ == "__main__":
    sys.exit(main())
