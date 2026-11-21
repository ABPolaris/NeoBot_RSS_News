---
type: fix
scope: delivery
packages:
  python@neobot-rss-news: patch
---

拉取失败、超时或取消时不再更新 `pull_at`，下一次请求会重新尝试拉取。
