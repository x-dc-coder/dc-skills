# GitHub PR 证据附件上传（截图 / 终端 PNG）

PR 正文需要截图或终端 PNG 时，唯一允许的托管路径是 **GitHub 用户附件（authenticated web attachment flow）**：
在 GitHub 网页端（issue/PR 正文编辑框）直接粘贴或拖入图片，由 GitHub 托管为
`user-attachments/assets/...` 链接，再把该链接写进 PR 正文。

## 安全红线（全部强制）

1. **绝不把临时图片提交进仓库**（截图、终端 PNG 属临时证据，不入 git）。
2. **绝不用 GitHub Releases 存放 PR 证据**（Releases 是发布产物通道）。
3. **绝不用外部图床**（匿名第三方托管不可审计、会失效）。
4. **绝不记录/泄露**：浏览器 cookies、CSRF token、S3 表单字段、上传请求头。
   网页附件流程在浏览器内完成认证，任何步骤都不需要把这些值写进日志、PR 或对话。

## 流程

1. 本地生成截图 / 终端 PNG（临时目录即可）。
2. 在对应 PR（或先开 draft PR）的正文编辑框粘贴/拖入图片，等待 GitHub 返回
   `https://github.com/user-attachments/assets/<id>` 形式的链接。
3. 把链接按需嵌入 PR 正文（`![说明](链接)` 或裸链接）。
4. 删除本地临时图片。
