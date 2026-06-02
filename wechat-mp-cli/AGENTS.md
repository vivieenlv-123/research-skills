# 给 AI 代理的工作流说明

本项目（`wechat-mp-cli`）设计为由 AI 代理驱动。当用户「甩来一篇微信文章链接，要求总结并归档」时，按以下标准流程执行。

## 标准流程

1. **取正文**

   ```bash
   wechat-mp fetch "<文章URL>" --format json --out /tmp/article.json
   ```

   - 若报错提示「环境异常 / 验证页」，请让用户复制全文或另存 HTML，改用 `--from-text`。

2. **阅读并总结**
   - 读取 `/tmp/article.json` 的 `text` 字段。
   - 写出：一句话摘要（oneline）+ 分点的详细总结。
   - 判断分类与标签。
   - 把总结写入 `/tmp/summary.md`（纯文本，列表用 `- ` 开头即可）。

3. **归档**（按用户偏好二选一或都做）
   - 本地库：

     ```bash
     wechat-mp save --from-json /tmp/article.json \
       --tags "标签1,标签2" --oneline "一句话摘要" --summary-file /tmp/summary.md
     ```

   - 飞书文档：

     ```bash
     wechat-mp lark-save --doc <document_id> --from-json /tmp/article.json \
       --category "分类" --heading "二级标题" --scenario "适用场景" \
       --tags "标签1,标签2" --summary-file /tmp/summary.md
     ```

4. **确认**：回读检查结果（本地用 `show`，飞书用 `lark-cli docs +fetch`），向用户汇报。

## 约定

- **总结质量由 AI 负责**，不要依赖 `analyze` 的规则摘要作为最终产物（它只是兜底）。
- 一次只处理用户明确给出的链接；不要自行抓取无关文章。
- 飞书 `lark-save` 默认追加到文档末尾。需要把文章插入到指定分类章节之间时，
  改用 `lark-cli docs +fetch --detail with-ids` 拿到锚点 block-id，再用
  `lark-cli docs +update --command block_insert_after` 精准插入。
- 链接积累多了，主动建议用户做一次「分类归并」：把「待分类」下的文章整理到对应章节。
