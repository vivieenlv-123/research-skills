# wechat-mp-cli

一个用于**收集、提取、总结、归档微信公众号文章**的命令行工具。配合 AI 代理使用效果最佳：CLI 负责把正文稳定取回来、做本地存档与检索、把结果写进飞书文档；高质量的总结、分类与洞察由 AI 完成。

> 合规说明：本工具只访问公开网页与公开文章页，不绕过登录、付费、权限或反爬限制。请将其用于个人学习与资料整理，遵守来源站点的服务条款。

## 设计理念：AI 与 CLI 分工

| 角色 | 负责 |
|------|------|
| **CLI（本工具）** | 取正文、清洗、本地存档、全文检索、把内容写进飞书文档 |
| **AI 代理** | 阅读正文、写高质量总结、判断分类与标签、回答你的追问 |

这样既稳（机械活交给脚本），又准（判断活交给 AI），还能随时调用（落库到本地或飞书）。

## 安装

需要 Python 3.10+。

```bash
cd wechat-mp-cli
python3 -m pip install -e .
```

安装后获得 `wechat-mp` 命令。

可选：飞书归档功能依赖 [`lark-cli`](https://github.com)，需自行安装并登录：

```bash
lark-cli config init           # 配置应用
lark-cli auth login --domain docx   # 用户授权
```

## 快速上手

```bash
# 1. 搜索公众号文章链接（公开搜索）
wechat-mp search "用户调研" --limit 10

# 2. 抓取一篇文章为 JSON（含标题、公众号、作者、正文）
wechat-mp fetch "https://mp.weixin.qq.com/s/xxxx" --format json --out /tmp/a.json

# 3.（AI 阅读 /tmp/a.json 后写出总结，存到 /tmp/summary.md）

# 4a. 存入本地文章库
wechat-mp save --from-json /tmp/a.json --tags "用户调研" \
  --oneline "一句话摘要" --summary-file /tmp/summary.md

# 4b. 或：归档进飞书文档
wechat-mp lark-save --doc <document_id> --from-json /tmp/a.json \
  --category "用户调研方法论" --heading "用户洞察方法" \
  --scenario "用户深度洞察，定性问答方法" \
  --tags "用户调研,文案" --summary-file /tmp/summary.md
```

## 命令参考

### 取数

| 命令 | 说明 |
|------|------|
| `search <关键词>` | 通过公开搜索发现 `mp.weixin.qq.com` 文章链接 |
| `fetch <url>` | 抓取单篇文章，导出 Markdown 或 JSON（`--format`） |
| `analyze <url\|file>` | 分析文章 URL，或本地 `.json/.html/.md/.txt`，输出规则摘要 |
| `batch <urls.txt>` | 批量抓取分析，逐篇导出到 `--out-dir` |

### 本地文章库

库默认在 `~/wechat-articles`，可用 `WECHAT_MP_LIBRARY` 或 `--library` 覆盖。

| 命令 | 说明 |
|------|------|
| `save` | 把「正文 + 总结 + 元信息」存为一篇 Markdown 并更新索引 |
| `list [--tag]` | 列出已存档文章 |
| `lib-search <关键词>` | 全文检索（标题/标签/摘要/正文） |
| `show <编号或标题>` | 取出某篇存档 |

### 飞书归档（需 lark-cli）

| 命令 | 说明 |
|------|------|
| `lark-init [--title] [--categories]` | 创建一个飞书文档作为收藏库，返回 document_id |
| `lark-save --doc <id> ...` | 把一篇文章 + 总结追加到飞书文档末尾 |

`lark-save` 的内容来源三选一：`--from-json`（推荐，fetch 的产物）、`--from-text`（本地正文文件）、`--url`（现抓）。
总结来源：`--summary` 或 `--summary-file`。

## 抓不到正文怎么办

微信对部分访问会返回「环境异常 / 完成验证后即可继续访问」的验证页。CLI 检测到后会明确报错。处理方式：

1. 在浏览器打开文章，复制全文存成 `.txt/.md`，用 `--from-text` 传入；
2. 浏览器另存网页为 `.html`，同样用 `--from-text`；
3. 配置 `HTTPS_PROXY` 走可正常访问的网络后重试。

无论哪种方式，后续的总结、存档、飞书归档流程都不受影响。

## 给 AI 代理的说明

见 [`AGENTS.md`](AGENTS.md)：约定了「甩链接 → 抓取 → 总结 → 归档」的标准流程，方便任意 AI 代理直接驱动本工具。

## 许可

MIT，见 [`LICENSE`](LICENSE)。
