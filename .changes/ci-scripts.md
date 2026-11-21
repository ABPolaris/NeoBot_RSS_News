---
type: chore
scope: ci
packages:
  python@neobot-rss-news: patch
---

修复 PR 门禁脚本的 lint 问题，让 QuailEgg 的 release 发布 PR 豁免人工评审门禁，并让发布工作流跳过依赖宿主测试环境的 verify。
