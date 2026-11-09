# NeoBot_RSS_News

NeoBot 插件：定期从 RSS 源获取新闻文章，缓存到本地 SQLite，并按权重、周期和格式私聊发送给接收者。

管理入口全部在网页面板的「插件页面」：订阅列表、接收者（QQ 号）、缓存文章和运行状态都在这里维护。全局周期、时间、格式与数量上限在「配置表单」中设置。

## 功能概览

- 订阅最多 7 条，可新增、修改、删除；权重 1–10 不可重复，不填时自动使用最小可用权重。
- 首次初始化写入两条预设（中新网要闻导读、IT之家）；用户删改后升级或重启都不会补回。
- 并发上限 4 拉取 RSS（`feedparser` + `lxml` 清洗正文），单源失败重试 3 次（共 4 次尝试）、间隔 30 秒、单次超时 30 秒，整轮耗时不超过拉取间隔的 3/4。
- 用户填写的 RSS 地址做 SSRF 防护：只允许 http/https，拒绝凭据、内网/回环/链路本地/保留地址，重定向逐跳校验。
- 按 `(source_id, pubdate DESC)` 增量缓存；超过保留天数的文章先清空标题/链接/正文/开头并标记 60 天后删除，到期时连同已发送记录一起彻底删除。
- 每个接收者按 `last_sent` / `next_send` 独立计算发送时间，配置变更时按需求伪代码重算；发送窗口为设定时间起 5 分钟，错过窗口不补发。
- 发送时按权重从低到高、订阅内从新到旧取文章；单篇 200 字开头、单条消息 3950 字，均按 Unicode 字符数截断。
- 支持三种发送格式和五种发送内容；多个接收者串行发送，消息之间按 1–5 秒配置间隔。
- 发送失败逐篇累计 `failed_cnt`；单轮失败 ≥5 篇或连续 3 轮全部失败时自动暂停该接收者并登记原因，用户重新启用时清空原因。
- **发送测试**：插件页面提供“发送测试”按钮，随机读取一篇缓存文章，按所选格式/内容发送到指定私聊 QQ；整个动作只读数据库与配置，不写已发送记录、接收者或配置文件。

## 目录结构

```text
NeoBot_RSS_News/
  __init__.py          # Plugin 实例、配置模型、生命周期、页面、说明书
  config.py            # Pydantic 配置与 HH:MM / 时区校验
  payloads.py          # 面板动作输入输出模型
  actions.py           # subscriptions.* / destinations.* / status.* 动作
  storage.py           # SQLite 表、事务、增量写入、清理与级联删除
  net.py               # SSRF 校验与带重定向防护的下载
  fetch.py             # feedparser 解析、guid 回退、3 次重试
  textutils.py         # lxml 正文清洗与 200 字开头
  composer.py          # 发送格式、3950 字切分、内容选项
  schedule.py          # next_send 与 5 分钟发送窗口
  scheduler.py         # 拉取/发送后台任务、失败阈值
  sender.py            # 宿主私聊发送端口
  runtime.py           # 运行时容器与配置热重载
  presets.py           # 首次初始化预设
  web/                 # 插件页面（原生 HTML/CSS/JS，无前端构建）
  tests/               # 离线业务测试（130 个）
  plugin.toml          # 插件清单、依赖、打包默认配置
  plugin.contract.json # 由 SDK export 生成
  .quailegg.toml       # QuailEgg 发版配置
```

## 安装与首次使用

需要 NeoBot 本体和 Python 3.13/3.14。插件声明的 Python 依赖会由宿主安装：`feedparser`、`lxml`、`httpx`、`aiosqlite`、`tzdata`、`pydantic`。

1. 把整个 `NeoBot_RSS_News/` 目录放进部署的 `plugins.dir`（默认 `plugins/`），重启 NeoBot。
2. 在面板「插件」中确认 `NeoBot_RSS_News` 已加载，打开「插件页面」。
3. 页面会显示两条预设订阅；可按需修改或删除。可以添加自己的 RSS 地址与权重。
4. 添加接收者 QQ 号（仅私聊，5–13 位、不以 0 开头）。格式合法不代表 QQ 有效，请先用该 QQ 私聊机器人确认能收到消息。
5. 打开「配置表单」，至少设置：启用定期发送、发送周期、发送时间、时区。保存后配置原地生效，不需要重启插件。
6. 保存后会立即拉取一次；之后按拉取间隔增量更新。可在页面「最近缓存文章」确认抓取结果。

> 默认 `send_enabled = false`，即不发送。只添加订阅和接收者不会产生任何私聊消息。

## 配置项

| 字段 | 默认 | 范围 / 取值 | 说明 |
| --- | --- | --- | --- |
| `send_enabled` | `false` | 开关 | 是否定期发送 |
| `send_every_days` | `1` | 1–7 | 每若干天发送一次 |
| `send_time` | `18:00` | `HH:MM` | 24 小时制；窗口为该时间起 5 分钟 |
| `timezone` | `Asia/Shanghai` | IANA 时区 | 发送时间所属时区 |
| `send_format` | `per_source` | `merged` / `per_source` / `per_article` | 尽可能合并 / 分订阅 / 分条 |
| `content_format` | `title_link_lead` | 五种 | 仅标题 / 标题和链接 / 标题和开头 / 标题和正文 / 标题、链接和开头 |
| `per_source_limit` | `10` | 1–100 | 每条订阅每轮最多发送篇数 |
| `per_destination_limit` | `50` | 1–500 | 每个接收者每轮接收总数 |
| `pull_interval_minutes` | `120` | 15–720 | RSS 拉取间隔 |
| `cache_days` | `10` | 7–30 | 文章缓存保留天数 |
| `message_interval_seconds` | `2` | 1–5 | 连续消息间隔 |

当「订阅数 × 每订阅上限」大于「每接收者文章总数」时，插件页面会显示“可能饿死”的警告；添加订阅或修改数量上限后也会提示。建议提高接收总数或降低每订阅上限。

## 数据与运行边界

- 配置：`plugins_data/NeoBot_RSS_News/config.toml`（宿主管理，升级不丢）。
- 数据库：`plugins_data/NeoBot_RSS_News/databases/rss_news.db`（SQLite，WAL）。
- 订阅表：`subscriptions(id, name, url UNIQUE, weight UNIQUE, created_at, pull_at)`。
- 文章表：`articles(source_id, guid, title, link, body, summary, pubdate, fetched_at, deleted_at)`，主键 `(source_id, guid)`，索引 `(source_id, pubdate DESC)`。
- 接收者表：`destinations(destination_id, last_sent, next_send, enabled, reason, consecutive_failed_rounds)`。
- 已发送表：`sent(destination_id, source_id, article_guid, failed_cnt, last_attempt_at, skipped)`。
- 删除订阅/接收者会级联删除文章与已发送记录；修改订阅 URL 等同删除后重建，并清空旧记录。

发送时间逻辑：

- 从未发送过：取配置时区“今天”的 HH:MM；若已不足 30 分钟则按 N 天顺延。
- 发送过：从 `last_sent` 起按 N 天步长对齐 HH:MM，并保证结果晚于当前时间 30 分钟。
- 开始时先推进 `last_sent` / `next_send`，再发送；重启后即使仍在窗口内也不会重复发送。
- 启动检查发现 `next_send` 已错过窗口时，直接快进到下一个可行时间，不补发。

## 开发与验收

以下命令在插件目录执行，假设插件与 NeoBot 源码同级；目录不同时把 `../NeoBot` 换成实际路径。当前环境使用 NeoBot 的 `.venv`：

```sh
uv sync --project ../NeoBot --all-packages --locked
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk check .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export . --check
uv run --project ../NeoBot --no-sync python ../NeoBot/scripts/plugin_ci.py . --output-dir ../NeoBot_RSS_News_acceptance
```

本次实际验收结果：`plugin_ci` 全部阶段通过，`130 passed`，两次构建 SHA256 一致，ZIP 安装、启动、停止、重启、重载与外部配置冒烟通过。

测试覆盖：配置校验、next_send 伪代码、正文清洗与 200 字截断、三种发送格式与 3950 字切分、SSRF 与重定向、feedparser 解析与 guid 回退、重试次数、SQLite 预设/上限/级联/60 天清理、调度器并发与失败阈值、动作权限与 CRUD、配置热重载。

打开隔离面板查看配置表单与插件页面：

```sh
uv run --project ../NeoBot --no-sync python ../NeoBot/scripts/plugin_demo.py --sample . --port 19981
```

演示不连接真实 QQ，不读取真实配置。真实 QQ 私聊、真实 RSS 站点与目标操作系统时区数据仍需在实际部署中验证。

## QuailEgg 发版

工程包含 `.quailegg.toml`、`.changes/` 和 `.github/workflows/qegg-*.yml`。`qegg check` 可识别 `python@neobot-rss-news` 包；发布前请把 `.quailegg.toml` 与 `pyproject.toml` 中的仓库地址改成实际地址（当前按 `ABPolaris/NeoBot_RSS_News` 配置），并在有 `gh` 的环境执行：

```sh
qegg check
qegg plan
```

`qegg check` 在当前环境因未安装 GitHub CLI `gh` 停在 `tool-missing`；这不是工程配置错误。插件包默认 `publish = false`，避免误发 PyPI。

## 故障排查

| 现象                       | 处理                                                     |
| ------------------------ | ------------------------------------------------------ |
| 页面提示插件未运行                | 在面板确认插件已启用、加载成功；查看插件错误信息                               |
| 拉取一直失败                   | 检查 RSS 地址是否为公网 http/https；查看日志中的 HTTP/网络错误类型           |
| 没有发送                     | 确认「启用定期发送」已打开、接收者 `enabled` 为 true、发送时间在窗口内；默认不发送      |
| 接收者被自动暂停                 | 页面会显示失败原因；检查机器人连接后点「继续」，原因与连续失败计数会清零                   |
| 保存配置后提示需要重启              | 本插件声明了配置热重载；若宿主未注册消费者，仍按宿主提示重载/重启                      |
| 点击「发送测试」提示“插件没有声明此操作”    | 页面资源已是新版但运行中的 Python 后端仍是旧版。在插件管理中重载插件或重启 NeoBot，再刷新页面 |
| 日志里出现大量 `aiosqlite` DEBUG | 这是宿主 DEBUG 日志：一条 SQL 会打出 cursor/execute/fetch/close 多行，且 `archive_memories`、`scheduled_tasks` 不是本插件。将日志级别调到 INFO 可隐藏；本插件发送检查 15 秒、拉取检查 20 秒、页面状态 60 秒 |
| 静态契约过期                   | 重新执行 `export .`，核对差异后再验收                               |
| 测试找不到 `neobot_modloader` | 使用 `uv run --project ../NeoBot --no-sync`，先同步宿主环境      |

完整 API 见 NeoBot 的 `docs/07-插件开发.md`、`docs/04-功能文档/插件SDK与工程验收.md`、`docs/04-功能文档/插件设置界面.md`。
