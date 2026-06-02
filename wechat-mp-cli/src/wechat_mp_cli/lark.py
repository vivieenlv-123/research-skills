"""飞书（Lark）集成：把微信文章的链接与总结归档进飞书文档。

本模块只做一件事：把结构化内容拼成飞书文档 XML，再通过本机已安装的 `lark-cli`
（docs v2 API）创建或追加到文档。lark-cli 的安装与登录由使用者自行完成，
本模块仅在调用时检测其是否可用。
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime


class LarkError(RuntimeError):
    pass


def ensure_lark_cli() -> str:
    path = shutil.which("lark-cli")
    if not path:
        raise LarkError(
            "未找到 lark-cli。请先安装并登录飞书 CLI：\n"
            "  - 安装后执行 `lark-cli config init` 配置应用\n"
            "  - 执行 `lark-cli auth login --domain docx` 完成用户授权"
        )
    return path


def run_lark(args: list[str]) -> str:
    ensure_lark_cli()
    result = subprocess.run(
        ["lark-cli", *args], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise LarkError(f"lark-cli 执行失败（exit {result.returncode}）：\n{message}")
    return result.stdout


def escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def summary_to_xml(summary: str) -> str:
    """把纯文本/简易 Markdown 总结转成飞书文档 XML。

    规则：以 "- " 或 "* " 开头的连续行合并为无序列表，其余非空行各成一个段落。
    """
    blocks: list[str] = []
    bullet_buffer: list[str] = []

    def flush_bullets() -> None:
        if bullet_buffer:
            items = "".join(f"<li>{escape_xml(item)}</li>" for item in bullet_buffer)
            blocks.append(f"<ul>{items}</ul>")
            bullet_buffer.clear()

    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line[:2] in ("- ", "* "):
            bullet_buffer.append(line[2:].strip())
        else:
            flush_bullets()
            blocks.append(f"<p>{escape_xml(line)}</p>")
    flush_bullets()
    return "".join(blocks) or "<p>（未提供总结）</p>"


def build_doc_skeleton(title: str, categories: list[str]) -> str:
    parts = [
        f"<title>{escape_xml(title)}</title>",
        "<callout emoji=\"📌\" background-color=\"light-blue\" border-color=\"blue\">"
        "<p>本文档持续收录值得保存的微信公众号文章。每条记录包含原文链接、来源、"
        "适用场景、总结与备注。建议配合 AI 代理持续整理、分类与归纳。</p></callout>",
    ]
    for category in categories:
        parts.append(f"<h1>{escape_xml(category)}</h1><p>（暂无）</p>")
    return "".join(parts)


def build_entry_xml(
    *,
    title: str,
    url: str,
    heading: str,
    category: str,
    account: str,
    author: str,
    publish_time: str,
    scenario: str,
    summary: str,
    tags: list[str],
) -> str:
    saved_at = datetime.now().strftime("%Y-%m-%d")
    meta_bits = [bit for bit in [category, account, author, publish_time] if bit]
    meta_line = " · ".join(meta_bits + [f"收录 {saved_at}"])
    tag_line = " ".join(f"#{tag}" for tag in tags)

    parts = [
        "<hr/>",
        f"<h2>{escape_xml(heading or title)}</h2>",
        f"<p><a href=\"{escape_xml(url)}\">{escape_xml(title)}</a></p>",
        f"<p><span text-color=\"gray\">{escape_xml(meta_line)}</span></p>",
    ]
    if scenario:
        parts.append(f"<p><b>适用场景</b>：{escape_xml(scenario)}</p>")
    if tag_line:
        parts.append(f"<p><b>标签</b>：{escape_xml(tag_line)}</p>")
    parts.append("<p><b>总结</b></p>")
    parts.append(summary_to_xml(summary))
    return "".join(parts)


def create_doc(title: str, categories: list[str]) -> str:
    content = build_doc_skeleton(title, categories)
    return run_lark(["docs", "+create", "--api-version", "v2", "--as", "user", "--content", content])


def append_entry(doc: str, entry_xml: str) -> str:
    return run_lark(
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--doc",
            doc,
            "--command",
            "append",
            "--content",
            entry_xml,
        ]
    )
