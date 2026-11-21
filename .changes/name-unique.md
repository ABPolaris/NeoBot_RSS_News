---
type: feat
scope: storage
packages:
  python@neobot-rss-news: minor
---

订阅名称增加唯一约束并禁止空白：v3 迁移会去掉历史名称中的空白、重名追加 `-2`/`-3`，新建和改名也会统一校验。
