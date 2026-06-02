from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from . import library
from . import lark


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SEARCH_URL = "https://duckduckgo.com/html/?q={query}"


@dataclass
class SearchResult:
    title: str
    url: str
    source: str


@dataclass
class Article:
    url: str
    title: str
    author: str
    account: str
    publish_time: str
    description: str
    text: str
    fetched_at: str


@dataclass
class Analysis:
    title: str
    url: str
    author: str
    account: str
    publish_time: str
    word_count: int
    estimated_reading_minutes: int
    summary: list[str]
    keywords: list[tuple[str, int]]
    headings: list[str]
    export_time: str


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._last_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._last_tag = tag
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag in {"p", "br", "div", "section", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = data.strip()
        if cleaned:
            self.parts.append(cleaned)

    def text(self) -> str:
        raw = " ".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s+", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return html.unescape(raw).strip()


def fetch_url(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        return fetch_url_with_curl(url, timeout)


def fetch_url_with_curl(url: str, timeout: int) -> str:
    command = [
        "curl",
        "--location",
        "--silent",
        "--show-error",
        "--http1.1",
        "--tlsv1.2",
        "--retry",
        "2",
        "--max-time",
        str(timeout),
        "--user-agent",
        USER_AGENT,
        "--header",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        url,
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or f"curl exited with {result.returncode}"
        raise RuntimeError(f"Network error: {message}")
    return result.stdout


def normalize_wechat_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        query = parse_qs(parsed.query)
        if "uddg" in query:
            return unquote(query["uddg"][0])
    return url


def strip_tags(value: str) -> str:
    parser = TextExtractor()
    parser.feed(value)
    return parser.text()


def search_articles(keyword: str, limit: int) -> list[SearchResult]:
    query = quote_plus(f"site:mp.weixin.qq.com/s {keyword}")
    page = fetch_url(SEARCH_URL.format(query=query))
    candidates: list[SearchResult] = []
    seen: set[str] = set()

    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.I | re.S):
        url = normalize_wechat_url(html.unescape(href))
        if "mp.weixin.qq.com" not in url or url in seen:
            continue
        seen.add(url)
        title = re.sub(r"\s+", " ", strip_tags(label)).strip()
        candidates.append(SearchResult(title=title or url, url=url, source="duckduckgo"))
        if len(candidates) >= limit:
            break

    return candidates


def extract_meta(page: str, *names: str) -> str:
    for name in names:
        patterns = [
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\'](.*?)["\']',
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, page, flags=re.I | re.S)
            if match:
                return html.unescape(match.group(1)).strip()
    return ""


def extract_js_string(page: str, name: str) -> str:
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*(['\"])(.*?)\1", page, flags=re.S)
    if not match:
        return ""
    value = match.group(2)
    if "\\" in value:
        value = value.encode("utf-8").decode("unicode_escape", errors="ignore")
    return html.unescape(value).strip()


def extract_article_body(page: str) -> str:
    match = re.search(
        r'<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>\s*</div>\s*</div>',
        page,
        flags=re.I | re.S,
    )
    if not match:
        match = re.search(r'<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>', page, flags=re.I | re.S)
    body = match.group(1) if match else page
    parser = TextExtractor()
    parser.feed(body)
    return parser.text()


VERIFICATION_MARKERS = ("环境异常", "完成验证后即可继续访问", "去验证", "请输入验证")


def looks_like_verification(page: str, text: str) -> bool:
    body = text.strip()
    if any(marker in page for marker in VERIFICATION_MARKERS) and len(body) < 200:
        return True
    return False


def fetch_article(url: str) -> Article:
    page = fetch_url(url)
    article = parse_article_page(page, url)
    if looks_like_verification(page, article.text):
        raise RuntimeError(
            "微信返回了「环境异常/验证」页，无法直接抓取正文。\n"
            "请改用以下任一方式：\n"
            "  1) 在浏览器打开文章，复制全文保存为 .txt/.md，再运行 save --from-text\n"
            "  2) 浏览器另存网页为 .html，再运行 save --from-text article.html\n"
            "  3) 配置 HTTPS_PROXY 走可正常访问的网络后重试"
        )
    return article


def parse_article_page(page: str, url: str) -> Article:
    title = extract_js_string(page, "msg_title") or extract_meta(page, "og:title", "twitter:title")
    description = extract_js_string(page, "msg_desc") or extract_meta(page, "description", "og:description")
    account = extract_js_string(page, "nickname") or extract_meta(page, "og:site_name")
    author = extract_js_string(page, "author")
    publish_time = extract_js_string(page, "publish_time")
    text = extract_article_body(page)

    return Article(
        url=url,
        title=title or "Untitled WeChat Article",
        author=author,
        account=account,
        publish_time=publish_time,
        description=description,
        text=text,
        fetched_at=now_iso(),
    )


def load_article(value: str) -> Article:
    path = Path(value)
    if path.exists():
        content = path.read_text(encoding="utf-8")
        file_url = path.resolve().as_uri()
        if path.suffix.lower() == ".json":
            data = json.loads(content)
            if "article" in data:
                data = data["article"]
            return Article(**data)
        if "<html" in content.lower() or "js_content" in content:
            return parse_article_page(content, file_url)
        return Article(
            url=file_url,
            title=path.stem,
            author="",
            account="",
            publish_time="",
            description="",
            text=content.strip(),
            fetched_at=now_iso(),
        )
    return fetch_article(value)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*|\n+", text)
    return [part.strip() for part in parts if len(part.strip()) >= 8]


def top_keywords(text: str, limit: int = 15) -> list[tuple[str, int]]:
    stopwords = {
        "一个",
        "以及",
        "因为",
        "所以",
        "但是",
        "没有",
        "这个",
        "这些",
        "我们",
        "他们",
        "你们",
        "可以",
        "进行",
        "通过",
        "如果",
        "不是",
        "更多",
        "the",
        "and",
        "for",
        "with",
    }
    words = re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
    filtered = [word for word in words if word not in stopwords and not word.isdigit()]
    return Counter(filtered).most_common(limit)


def find_headings(text: str, limit: int = 12) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        line = line.strip(" #\t")
        if 4 <= len(line) <= 32 and not re.search(r"[。！？!?]$", line):
            headings.append(line)
        if len(headings) >= limit:
            break
    return headings


def analyze_article(article: Article) -> Analysis:
    sentences = split_sentences(article.text)
    summary = sentences[:5]
    word_count = len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", article.text))
    return Analysis(
        title=article.title,
        url=article.url,
        author=article.author,
        account=article.account,
        publish_time=article.publish_time,
        word_count=word_count,
        estimated_reading_minutes=max(1, round(word_count / 450)),
        summary=summary,
        keywords=top_keywords(article.text),
        headings=find_headings(article.text),
        export_time=now_iso(),
    )


def render_markdown(article: Article, analysis: Analysis) -> str:
    keywords = "、".join(f"{word}({count})" for word, count in analysis.keywords)
    summary = "\n".join(f"- {item}" for item in analysis.summary) or "- 未提取到摘要句。"
    headings = "\n".join(f"- {item}" for item in analysis.headings) or "- 未识别到明显小标题。"
    return f"""# {article.title}

原文：{article.url}

公众号：{article.account or "未知"}
作者：{article.author or "未知"}
发布时间：{article.publish_time or "未知"}
抓取时间：{article.fetched_at}

## 分析概览

- 字数估算：{analysis.word_count}
- 预计阅读：{analysis.estimated_reading_minutes} 分钟
- 高频关键词：{keywords or "未提取到关键词"}

## 摘要

{summary}

## 文章结构

{headings}

## 正文

{article.text}
"""


def write_output(path: str | None, content: str) -> None:
    if not path:
        print(content)
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote {output}")


def write_json(path: str | None, data: object) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2)
    write_output(path, content)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def cmd_search(args: argparse.Namespace) -> int:
    results = search_articles(args.keyword, args.limit)
    if args.format == "json":
        write_json(args.out, [asdict(item) for item in results])
    else:
        lines = [f"# 搜索结果：{args.keyword}", ""]
        lines.extend(f"{idx}. [{item.title}]({item.url})" for idx, item in enumerate(results, 1))
        write_output(args.out, "\n".join(lines))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    article = fetch_article(args.url)
    if args.format == "json":
        write_json(args.out, asdict(article))
    else:
        write_output(args.out, render_markdown(article, analyze_article(article)))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    article = load_article(args.input)
    analysis = analyze_article(article)
    if args.format == "json":
        write_json(args.out, {"article": asdict(article), "analysis": asdict(analysis)})
    else:
        write_output(args.out, render_markdown(article, analysis))
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    urls = [line.strip() for line in Path(args.file).read_text(encoding="utf-8").splitlines()]
    urls = [line for line in urls if line and not line.startswith("#")]
    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, str]] = []
    for idx, url in enumerate(urls, 1):
        article = fetch_article(url)
        analysis = analyze_article(article)
        stem = safe_filename(f"{idx:02d}-{article.title}")[:80]
        report_path = output_dir / f"{stem}.md"
        report_path.write_text(render_markdown(article, analysis), encoding="utf-8")
        index.append({"title": article.title, "url": url, "report": str(report_path)})
        print(f"[{idx}/{len(urls)}] {article.title}")
        time.sleep(args.delay)

    write_json(str(output_dir / "index.json"), index)
    return 0


def safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\s]+", "-", value.strip())
    return value.strip("-") or "wechat-article"


def read_text_arg(value: str | None, file_value: str | None) -> str:
    if value:
        return value
    if file_value:
        return Path(file_value).read_text(encoding="utf-8")
    return ""


def cmd_save(args: argparse.Namespace) -> int:
    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        if "article" in data:
            data = data["article"]
        article = Article(**data)
    elif args.from_text:
        article = load_article(args.from_text)
    elif args.url:
        article = fetch_article(args.url)
    else:
        print("Error: 需要 --from-json / --from-text / --url 之一", file=sys.stderr)
        return 1

    summary = read_text_arg(args.summary, args.summary_file)
    tags = [tag.strip() for tag in (args.tags or "").split(",") if tag.strip()]
    root = library.library_root(args.library)
    entry = library.save_article(
        root,
        title=args.title or article.title,
        account=args.account or article.account,
        author=args.author or article.author,
        url=args.url or article.url,
        publish_time=args.publish_time or article.publish_time,
        tags=tags,
        oneline=args.oneline or article.description,
        summary=summary,
        text=article.text,
    )
    print(f"已存档：[{entry.id}] {entry.title}")
    print(f"位置：{entry.path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = library.library_root(args.library)
    entries = library.load_index(root)
    if args.tag:
        entries = [e for e in entries if args.tag in e.tags]
    if not entries:
        print("文章库为空。")
        return 0
    print(f"# 文章库（{root}）共 {len(entries)} 篇\n")
    for entry in entries:
        tags = " ".join(f"#{t}" for t in entry.tags)
        print(f"[{entry.id}] {entry.title}")
        meta = " | ".join(filter(None, [entry.account, entry.publish_time, tags]))
        if meta:
            print(f"    {meta}")
        if entry.oneline:
            print(f"    {entry.oneline}")
    return 0


def cmd_libsearch(args: argparse.Namespace) -> int:
    root = library.library_root(args.library)
    matches = library.search_entries(root, args.keyword)
    if not matches:
        print(f"没有匹配「{args.keyword}」的文章。")
        return 0
    print(f"# 匹配「{args.keyword}」共 {len(matches)} 篇\n")
    for entry in matches:
        print(f"[{entry.id}] {entry.title}")
        if entry.oneline:
            print(f"    {entry.oneline}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = library.library_root(args.library)
    entry = library.find_entry(root, args.id)
    if not entry:
        print(f"Error: 找不到文章「{args.id}」", file=sys.stderr)
        return 1
    record = Path(entry.path)
    if not record.exists():
        print(f"Error: 记录文件丢失：{entry.path}", file=sys.stderr)
        return 1
    print(record.read_text(encoding="utf-8"))
    return 0


def cmd_lark_init(args: argparse.Namespace) -> int:
    categories = [c.strip() for c in (args.categories or "").split(",") if c.strip()]
    if not categories:
        categories = ["📁 待分类", "🤖 AI 与效率", "👥 团队与管理", "📈 行业洞察"]
    output = lark.create_doc(args.title, categories)
    print(output.strip())
    print("\n提示：从上面 JSON 的 data.document.url / document_id 取得文档地址，"
          "后续用 `wechat-mp lark-save --doc <document_id> ...` 归档文章。")
    return 0


def cmd_lark_save(args: argparse.Namespace) -> int:
    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        if "article" in data:
            data = data["article"]
        article = Article(**data)
    elif args.from_text:
        article = load_article(args.from_text)
    elif args.url:
        article = fetch_article(args.url)
    else:
        print("Error: 需要 --from-json / --from-text / --url 之一", file=sys.stderr)
        return 1

    summary = read_text_arg(args.summary, args.summary_file)
    tags = [tag.strip() for tag in (args.tags or "").split(",") if tag.strip()]
    entry_xml = lark.build_entry_xml(
        title=args.title or article.title,
        url=args.url or article.url,
        heading=args.heading or args.title or article.title,
        category=args.category or "",
        account=args.account or article.account,
        author=args.author or article.author,
        publish_time=args.publish_time or article.publish_time,
        scenario=args.scenario or "",
        summary=summary,
        tags=tags,
    )
    lark.append_entry(args.doc, entry_xml)
    print(f"已归档到飞书文档：{args.doc}")
    print(f"标题：{args.title or article.title}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat-mp", description="Search and analyze public WeChat articles.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search public web results for WeChat article links.")
    search.add_argument("keyword")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--format", choices=["markdown", "json"], default="markdown")
    search.add_argument("--out")
    search.set_defaults(func=cmd_search)

    fetch = subparsers.add_parser("fetch", help="Fetch one WeChat article and export it.")
    fetch.add_argument("url")
    fetch.add_argument("--format", choices=["markdown", "json"], default="markdown")
    fetch.add_argument("--out")
    fetch.set_defaults(func=cmd_fetch)

    analyze = subparsers.add_parser("analyze", help="Analyze an article URL or a fetched JSON file.")
    analyze.add_argument("input")
    analyze.add_argument("--format", choices=["markdown", "json"], default="markdown")
    analyze.add_argument("--out")
    analyze.set_defaults(func=cmd_analyze)

    batch = subparsers.add_parser("batch", help="Analyze URLs from a text file.")
    batch.add_argument("file")
    batch.add_argument("--out-dir", default="reports")
    batch.add_argument("--delay", type=float, default=1.5)
    batch.set_defaults(func=cmd_batch)

    save = subparsers.add_parser("save", help="Save an article (with a summary) into the local library.")
    save.add_argument("--from-json", help="fetch 输出的 JSON 文件")
    save.add_argument("--from-text", help="本地 .txt/.md/.html 正文文件")
    save.add_argument("--url", help="直接抓取该链接")
    save.add_argument("--title")
    save.add_argument("--account")
    save.add_argument("--author")
    save.add_argument("--publish-time", dest="publish_time")
    save.add_argument("--tags", help="逗号分隔，如 AI,团队管理")
    save.add_argument("--oneline", help="一句话摘要，用于列表展示")
    save.add_argument("--summary", help="总结正文（Markdown）")
    save.add_argument("--summary-file", help="从文件读取总结")
    save.add_argument("--library", help="文章库目录，默认 ~/wechat-articles")
    save.set_defaults(func=cmd_save)

    list_cmd = subparsers.add_parser("list", help="List archived articles.")
    list_cmd.add_argument("--tag")
    list_cmd.add_argument("--library")
    list_cmd.set_defaults(func=cmd_list)

    libsearch = subparsers.add_parser("lib-search", help="Search the local library.")
    libsearch.add_argument("keyword")
    libsearch.add_argument("--library")
    libsearch.set_defaults(func=cmd_libsearch)

    show = subparsers.add_parser("show", help="Show an archived article by id or title.")
    show.add_argument("id")
    show.add_argument("--library")
    show.set_defaults(func=cmd_show)

    lark_init = subparsers.add_parser("lark-init", help="Create a Feishu doc as the article library (via lark-cli).")
    lark_init.add_argument("--title", default="微信文章收藏库")
    lark_init.add_argument("--categories", help="逗号分隔的分类章节，默认给一组常用分类")
    lark_init.set_defaults(func=cmd_lark_init)

    lark_save = subparsers.add_parser("lark-save", help="Append an article + summary to a Feishu doc (via lark-cli).")
    lark_save.add_argument("--doc", required=True, help="飞书文档 URL 或 document_id")
    lark_save.add_argument("--from-json", help="fetch 输出的 JSON 文件")
    lark_save.add_argument("--from-text", help="本地 .txt/.md/.html 正文文件")
    lark_save.add_argument("--url", help="直接抓取该链接")
    lark_save.add_argument("--title")
    lark_save.add_argument("--heading", help="二级标题，默认用文章标题")
    lark_save.add_argument("--category", help="分类（写入元信息行）")
    lark_save.add_argument("--scenario", help="适用场景")
    lark_save.add_argument("--account")
    lark_save.add_argument("--author")
    lark_save.add_argument("--publish-time", dest="publish_time")
    lark_save.add_argument("--tags", help="逗号分隔")
    lark_save.add_argument("--summary", help="总结正文（纯文本/简易 Markdown）")
    lark_save.add_argument("--summary-file", help="从文件读取总结")
    lark_save.set_defaults(func=cmd_lark_save)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return args.func(args)
    except lark.LarkError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
