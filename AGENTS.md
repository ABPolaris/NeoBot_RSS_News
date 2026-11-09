# 插件开发约定

本工程是 NeoBot 的独立 Python 插件 `NeoBot_RSS_News`。先阅读 `README.md`，再修改业务。

## 结构约束

- 入口是 `__init__.py` 导出的 `plugin` 实例，不使用旧的 setup、Matcher 或 on_command API。
- 管理入口是插件页面与配置表单，不额外注册聊天命令。页面业务只通过 `actions.py` 声明的 `plugin.action` 访问；动作必须声明 `permission`（读取用 `read`，修改用 `manage`）。
- 普通配置在 `config.py` 的 `Config` 中声明；面板自动生成表单。运行时通过新配置消费者原地生效，不要要求重启。
- 网关抓取只在 `net.py` / `fetch.py` 中做：任何用户填写的 URL 都必须先过 `validate_feed_url`，不要绕过 SSRF 校验直接请求。
- 数据库访问只在 `storage.py` 中做；后台任务与动作共用同一把存储锁。不要在动作或调度器里直接拼 SQL。
- 后台任务使用 `ctx.create_task` 托管；停止时必须取消并等待结束。发送必须串行并遵守消息间隔。
- 修改发送格式、内容选项或 3950/200 字规则时，同时更新 `composer.py` 与 `textutils.py` 的测试。
- `system.send_test` 必须保持只读：只允许 `store.random_article()` 读取缓存，再用 `PrivateMessageSender` 直接发送；不要调用 `mark_send_started`、`record_sent`/`record_failure`，不要保存配置。
- 中文注释解释约束与原因；避免无关重构和过度防御。

## 配置、数据与教程

- 配置和数据库属于宿主 `plugins_data/NeoBot_RSS_News/`，不写回 `plugin.toml` 或源码目录。
- 不提交真实凭据、QQ 号、RSS 私密地址或用户数据；测试只用合成数据与保留地址（如 `93.184.216.34`）。
- `plugin.contract.json` 只由 `export` 生成；修改配置字段、动作或页面锚点时同步重新导出，并检查 `plugin.toml` 的 `sdk.pages` 与 view entry 一致。
- 说明书只引用 `config.*` 目标；页面锚点必须与 `web/index.html` 中的 ID 一致。
- 预设订阅只在首次初始化写入；不要在任何升级/启动路径补写，用户删改后必须保持删除状态。

## 验证与交付

在插件目录使用宿主环境命令：

```sh
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk check .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export . --check
uv run --project ../NeoBot --no-sync python ../NeoBot/scripts/plugin_ci.py . --output-dir ../NeoBot_RSS_News_acceptance
uv run --project ../NeoBot --no-sync python -m pytest -q
```

- 业务测试必须覆盖成功结果、关键失败和权限；使用合成宿主端口，真实执行 SDK 注册、动作和存储。
- 不得删除、跳过、xfail 或放宽失败断言来换取通过。
- 错误先看 `report.json` 的 `errors` 与对应阶段日志。
- 只报告真正执行过的命令、结果与未验证范围；真实 QQ、真实 RSS 站点和浏览器交互不属于当前离线测试的证明范围。
- QuailEgg 配置在 `.quailegg.toml`；发布前核对仓库地址与包名，不要擅自改版本号或推送。
