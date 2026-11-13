# 插件开发约定

本工程是 NeoBot 的独立 Python 插件 `NeoBot_RSS_News`。先阅读 `README.md`，再修改业务。

## 结构约束

- 入口是 `__init__.py` 导出的 `plugin` 实例，不使用旧的 setup、Matcher 或 on_command API。
- 聊天入口在 `commands.py`：`/RSS新闻`、`/所有RSS新闻`、`/RSS列表`、`/设置昵称`。新闻类指令只能私聊，群聊只返回“新闻仅支持在私聊中查看。”；`设置昵称` 使用宿主 `permission=1`，群聊可用。
- 面板业务只通过 `actions.py` 声明的 `plugin.action` 访问。动作权限：读取用 `read`，修改用 `manage`。
- 插件**没有定时器、后台轮询、发送窗口、接收者表或已发送表**。拉取只发生在聊天指令触发的 `delivery.py` 中，不要重新引入 scheduler/next_send 结构。
- 拉取按源检查 `pull_at` 与 `pull_interval_seconds`；`/RSS新闻` 只检查该源，`/所有RSS新闻` 检查全部到期源。并发上限 4，重试/超时/SSRF 规则沿用 `fetch.py`、`net.py`。
- 数据库访问只在 `storage.py` 中做；不要绕过 `RssNewsStore` 直接拼 SQL。`destinations`/`sent` 已删除，`articles.deleted_at` 已移除，超期文章直接删除。
- 订阅简称（nickname）允许为空但不可重复；昵称唯一性由存储层检查，命令和面板都必须给出中文错误。
- `system.send_test` 必须保持只读：只允许 `store.random_article()` 读取缓存，直接发送；不要写入文章、订阅或配置。
- 后台发送任务只用 `ctx.create_task` 托管；停止时由插件生命周期取消，不要自己保留未托管的长时间任务。

## 配置、数据与教程

- 配置在 `config.py`：`send_enabled`（静音开关）、`content_format`、`per_source_limit`、`per_destination_limit`、`pull_interval_minutes`、`cache_days`、`message_interval_seconds`。旧版 `send_time`/`send_every_days`/`timezone`/`send_format` 必须继续被忽略，避免升级后配置无法加载。
- 配置和数据库属于宿主 `plugins_data/NeoBot_RSS_News/`，不写回源码。
- 旧库迁移在 `storage.py`：版本 2 删除 `destinations`/`sent`、重建不含 `deleted_at` 的 `articles`、增加 `nickname` 并只为既有预设补一次简称。用户清空简称后不得在重启时补回。
- 不提交真实凭据、QQ 号、RSS 私密地址或用户数据；测试只用合成数据与保留地址。
- `plugin.contract.json` 只由 `export` 生成；修改配置字段、动作或页面锚点时同步重新导出。
- 说明书只引用 `config.*` 目标；页面锚点必须与 `web/index.html` 的 ID 一致。

## 验证与交付

在插件目录使用宿主环境命令：

```sh
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk check .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export . --check
uv run --project ../NeoBot --no-sync python ../NeoBot/scripts/plugin_ci.py . --output-dir ../NeoBot_RSS_News_acceptance
uv run --project ../NeoBot --no-sync python -m pytest -q
```

- 业务测试必须覆盖成功结果、关键失败、权限和旧库迁移；使用合成宿主端口，真实执行 SDK 注册、指令 DSL、动作和存储。
- 不得删除、跳过、xfail 或放宽失败断言来换取通过。
- 错误先看 `report.json` 的 `errors` 与对应阶段日志。
- 只报告真正执行过的命令、结果与未验证范围；真实 QQ、真实 RSS 站点和浏览器交互不属于当前离线测试的证明范围。
- QuailEgg 配置在 `.quailegg.toml`；发布前核对仓库地址与包名，不要擅自改版本号或推送。
