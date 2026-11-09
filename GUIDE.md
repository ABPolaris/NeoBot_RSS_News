# 编写插件使用说明

当前 `__init__.py` 的 `plugin.guide("first-use", ...)` 只讲配置表单：开启发送、周期、时间、格式、内容与数量上限。订阅和接收者属于插件页面的真实业务数据，教程不创建真实记录，因此没有把页面增删步骤写进说明书。

## 当前工程的目标

| 目标 | 用途 |
| --- | --- |
| `config` | 展示完整配置表单与模拟保存位置 |
| `config.send_enabled` | 高亮“启用定期发送” |
| `config.send_every_days` | 高亮发送周期 |
| `config.send_time` | 高亮 HH:MM 发送时间 |
| `config.send_format` | 高亮消息合并格式 |
| `config.content_format` | 高亮文章内容选项 |
| `config.per_source_limit` | 高亮每订阅篇数上限（同时说明饿死警告） |

页面已声明但教程尚未使用的锚点：`view.management.status`、`view.management.subscriptions`、`view.management.subscription-form`、`view.management.destinations`、`view.management.destination-form`、`view.management.send-test`、`view.management.articles`。

## 扩展规则

| 声明 | 教程目标 |
| --- | --- |
| `Config` 新增 `interval` 字段 | `config.interval` |
| 嵌套配置 `delivery.enabled` | `config.delivery.enabled` |
| `plugin.action("status", ...)` | `action.status` |
| `plugin.resource("items", ...)` | `resource.items` |
| 页面 `management` 锚点 `new` | `view.management.new` |

目标必须真实存在于导出的 `plugin.contract.json`。页面锚点的 ID 必须与 `web/index.html` 中的元素 ID 一致；页面 ID 与锚点 ID 都不能含点号。

## 改写配置步骤

1. `chapter` 写使用者要完成的事情；`text` 写位置、输入和预期结果。
2. `target` 使用稳定目标；字段显示名称改了也不必改变 ID。
3. `form` 可用 `start/edit/validate/save/saved`，只能与 `config` / `config.<字段>` 一起使用。`validate` 只演示样例范围，真实保存仍由服务端校验。
4. 每步 `example` 是完整独立快照，不继承上一步；缺省值只取公开默认值或空值。不要把真实 QQ、RSS 地址或配置写进样例。
5. 说明书不会执行真实动作，也不会保存真实配置；演示页面的动作结果必须通过 `demo={"actions": {...}}` 显式提供，缺少样例的动作会失败。

如果要在说明书中演示页面，先在 `plugin.guide(..., demo={"actions": {...}})` 里为 `subscriptions.list`、`destinations.list`、`status.get`、`articles.list` 提供合成结果，再在页面 `onGuideStep` 中重建完整快照。当前实现刻意不引入这一层，避免教程出现假的订阅或接收者。

## 验收清单

- 从第一步能看懂入口、所需条件和预期结果。
- 打开“目录与全文”能找到各章与说明。
- 后退、跳章、重播时样例状态正确，不残留上一步内容。
- 说明中不出现真实配置或用户数据，不执行真实写入。
- 修改配置字段或动作后重新执行 `export`，确认契约没有过期。

完成后执行 README 的 `export`、`check` 与 `plugin_ci`，再打开隔离面板逐项检查。
