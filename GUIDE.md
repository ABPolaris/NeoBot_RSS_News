# 编写插件使用说明

当前 `__init__.py` 的 `plugin.guide("first-use", ...)` 讲配置表单：静音开关、拉取间隔、发送内容、数量上限、缓存与消息间隔，最后给出 `/RSS新闻` 与 `/RSS列表` 的聊天样例。订阅增删、简称设置属于真实业务数据，教程不创建真实记录。

## 当前工程的目标

| 目标 | 用途 |
| --- | --- |
| `config` | 展示完整配置表单或模拟保存位置 |
| `config.send_enabled` | 高亮“允许发送新闻”（静音开关） |
| `config.pull_interval_minutes` | 高亮两次拉取最小间隔 |
| `config.content_format` | 高亮文章内容选项 |
| `config.per_source_limit` | 高亮每订阅篇数 |
| `config.per_destination_limit` | 高亮用户接收文章最大数量 |
| `config.cache_days` | 高亮缓存保留天数 |
| `config.message_interval_seconds` | 高亮多条消息间隔 |

页面已声明但教程尚未使用的锚点：`view.management.status`、`view.management.subscriptions`、`view.management.subscription-form`、`view.management.send-test`、`view.management.commands`、`view.management.articles`。

## 扩展规则

| 声明 | 教程目标 |
| --- | --- |
| `Config` 新增 `interval` 字段 | `config.interval` |
| 嵌套配置 `delivery.enabled` | `config.delivery.enabled` |
| `plugin.action("status", ...)` | `action.status` |
| `plugin.resource("items", ...)` | `resource.items` |
| 页面 `management` 锚点 `new` | `view.management.new` |

目标必须真实存在于导出的 `plugin.contract.json`。页面锚点 ID 必须与 `web/index.html` 中的元素 ID 一致；页面 ID 与锚点 ID 都不能含点号。

## 改写配置步骤

1. `chapter` 写使用者要完成的事情；`text` 写位置、输入和预期结果。
2. `target` 使用稳定目标；字段显示名称改了也不必改变 ID。
3. `form` 可用 `start/edit/validate/save/saved`，只能与 `config` / `config.<字段>` 一起使用。`validate` 只演示样例范围，真实保存仍由服务端校验。
4. 每步 `example` 是完整独立快照，不继承上一步；缺省值只取公开默认值或空值。不要把真实 QQ、RSS 地址或配置写进样例。
5. 聊天步骤用 `command={"text": "/RSS新闻 中新网", "context": "在与机器人的私聊中发送", "reply": "（按配置发送最近文章）"}`；不能与 `form` 混用。命令名仍应提示用户从 `/help` 核对。
6. 说明书不会执行真实动作，也不会保存真实配置；页面演示的动作结果必须通过 `demo={"actions": {...}}` 显式提供。

## 验收清单

- 从第一步能看懂入口、所需条件和预期结果。
- 打开“目录与全文”能找到各章、说明和命令。
- 后退、跳章、重播时样例状态正确，不残留上一步内容。
- 说明中不出现真实配置或用户数据，不执行真实写入。
- 修改配置字段、动作或页面锚点后重新执行 `export`，确认契约没有过期。

完成后执行 README 的 `export`、`check` 与 `plugin_ci`，再打开隔离面板逐项检查。
