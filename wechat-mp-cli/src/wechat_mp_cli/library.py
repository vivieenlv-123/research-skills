"""本地微信文章库：存档、检索、读取。

设计原则：CLI 只负责把正文取回来、存档、检索；高质量总结由调用方（Agent）写入。
库默认放在 WECHAT_MP_LIBRARY 指向的目录，否则用 ~/wechat-articles。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class LibraryEntry:
    id: str
    title: str
    account: str
    author: str
    url: str
    publish_time: str
    saved_at: str
    tags: list[str] = field(default_factory=list)
    oneline: str = ""
    path: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def library_root(override: str | None = None) -> Path:
    if override:
        root = Path(override).expanduser()
    elif os.environ.get("WECHAT_MP_LIBRARY"):
        root = Path(os.environ["WECHAT_MP_LIBRARY"]).expanduser()
    else:
        root = Path.home() / "wechat-articles"
    (root / "articles").mkdir(parents=True, exist_ok=True)
    return root


def index_path(root: Path) -> Path:
    return root / "index.json"


def load_index(root: Path) -> list[LibraryEntry]:
    path = index_path(root)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [LibraryEntry(**item) for item in data]


def save_index(root: Path, entries: list[LibraryEntry]) -> None:
    payload = [asdict(entry) for entry in entries]
    index_path(root).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def slugify(value: str, fallback: str = "article") -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "", value).strip()
    value = re.sub(r"\s+", "-", value)
    return value[:60] or fallback


def make_id(root: Path, title: str) -> str:
    date = datetime.now().strftime("%Y%m%d")
    base = f"{date}-{slugify(title)}"
    existing = {entry.id for entry in load_index(root)}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


def render_record(entry: LibraryEntry, summary: str, text: str) -> str:
    tags = " ".join(f"#{tag}" for tag in entry.tags) or "（无）"
    return f"""# {entry.title}

- 编号：{entry.id}
- 公众号：{entry.account or "未知"}
- 作者：{entry.author or "未知"}
- 发布时间：{entry.publish_time or "未知"}
- 原文：{entry.url or "未知"}
- 存档时间：{entry.saved_at}
- 标签：{tags}

## 总结

{summary.strip() or "（未提供总结）"}

## 正文

{text.strip() or "（未保存正文）"}
"""


def save_article(
    root: Path,
    *,
    title: str,
    account: str,
    author: str,
    url: str,
    publish_time: str,
    tags: list[str],
    oneline: str,
    summary: str,
    text: str,
) -> LibraryEntry:
    entry_id = make_id(root, title or "wechat-article")
    record_path = root / "articles" / f"{entry_id}.md"
    entry = LibraryEntry(
        id=entry_id,
        title=title or "未命名文章",
        account=account,
        author=author,
        url=url,
        publish_time=publish_time,
        saved_at=now_iso(),
        tags=tags,
        oneline=oneline,
        path=str(record_path),
    )
    record_path.write_text(render_record(entry, summary, text), encoding="utf-8")
    entries = load_index(root)
    entries.insert(0, entry)
    save_index(root, entries)
    return entry


def find_entry(root: Path, key: str) -> LibraryEntry | None:
    entries = load_index(root)
    for entry in entries:
        if entry.id == key:
            return entry
    for entry in entries:
        if key.lower() in entry.title.lower():
            return entry
    return None


def search_entries(root: Path, keyword: str) -> list[LibraryEntry]:
    keyword = keyword.lower()
    matches: list[LibraryEntry] = []
    for entry in load_index(root):
        haystack = " ".join(
            [entry.title, entry.account, entry.author, entry.oneline, " ".join(entry.tags)]
        ).lower()
        if keyword in haystack:
            matches.append(entry)
            continue
        record = Path(entry.path)
        if record.exists() and keyword in record.read_text(encoding="utf-8").lower():
            matches.append(entry)
    return matches
