# NeoBot_RSS_News

NeoBot 插件：**不再主动或定时发送**，只在用户私聊请求时按需拉取 RSS 并发送文章。

管理入口在网页面板的「插件页面」：订阅、简称、缓存文章和发送测试都在这里维护；全局内容、数量、拉取间隔与静音开关在「配置表单」中设置。聊天入口是 NeoBot 指令：

```text
/RSS新闻 [源名称/简称]
/所有RSS新闻
/RSS列表
/设置昵称 订阅全称 [订阅简称]
```

新闻类指令只支持私聊；在群聊中请求 `/RSS新闻` 或 `/所有RSS新闻` 只会收到「新闻仅支持在私聊中查看。」。`/RSS列表` 和 `/设置昵称` 在群聊中可用，后者仅限次级管理员以上。

## 功能概览

- 订阅最多 7 条；可新增、修改、删除。权重 1–10 不可重复，不填时自动使用最小可用权重。
- 每条订阅有名称、简称（昵称）、URL、权重；简称可为空、不可重复。预设：
  - 中新网要闻导读（中新网）
  - IT之家（之家）
- 首次初始化写入预设；用户删改后升级或重启不会补回。
- **按需拉取**：`/RSS新闻` 检查该源上次拉取时间，`/所有RSS新闻` 检查所有源；距上次拉取不足「两次拉取最小间隔」时直接读缓存，否则先去重拉取再发送。
- 拉取并发上限 4；单源失败重试 3 次（共 4 次尝试）、间隔 30 秒、单次超时 30 秒；整轮耗时不超过拉取间隔的 3/4。
- 用户填写的 RSS 地址做 SSRF 防护：只允许 http/https，拒绝凭据、内网/回环/链路本地/保留地址，重定向逐跳校验。
- 文章清洗仍使用 `lxml`，丢弃图片/脚本等资源；200 字开头和 3950 字消息限制保持不变。
- `/所有RSS新闻` 按权重由小到大逐订阅发送，每个订阅的文章合成消息（超过 3950 字会拆分），**每条消息最前面加“源名称（源简称）：”**；消息间隔受「多条消息间隔」限制，总数受「用户接收文章最大数量」限制。
- **不再有接收者表和已发送表**：每次请求都会重新发送该订阅当前缓存中最近的若干篇，不跨请求去重。
- `/设置昵称` 可设置或清空简称；简称重复时拒绝修改并报告。

## 目录结构

```text
NeoBot_RSS_News/
  __init__.py          # Plugin 实例、生命周期、页面、说明书
  config.py            # Pydantic 配置
  commands.py          # 聊天指令与参数解析
  delivery.py          # 按需拉取、过期清理与私聊发送
  actions.py           # 面板动作
  payloads.py          # 动作输入输出模型
  storage.py           # SQLite、旧库迁移、订阅/文章查询
  net.py               # SSRF 校验与下载
  fetch.py             # feedparser 解析、guid 回退、重试
  textutils.py         # lxml 正文清洗与 200 字开头
  composer.py          # 3950 字切分与内容组装
  sender.py            # 宿主私聊发送端口
  runtime.py           # 运行时容器与配置热重载
  presets.py           # 预设订阅与预设简称
  web/                 # 插件页面（原生 HTML/CSS/JS）
  tests/               # 离线业务测试
```

## 安装与首次使用

需要 NeoBot 本体与 Python 3.13/3.14。插件声明的 Python 依赖由宿主安装：`feedparser`、`lxml`、`httpx`、`aiosqlite`、`pydantic`。

1. 把整个 `NeoBot_RSS_News/` 目录放进部署的 `plugins.dir`（默认 `plugins/`），重启 NeoBot。
2. 在面板「插件」确认已加载，打开「插件页面」查看预设订阅与简称。
3. 在配置表单确认「允许发送新闻」已打开（旧版 `send_enabled = false` 升级后会被当作静音，需要手动打开）。
4. 在私聊发送 `/RSS列表` 查看名称与简称，再发送 `/RSS新闻 中新网` 或 `/所有RSS新闻`。
5. 需要验证「读取数据库 → 私聊发送」链路时，点页面「发送测试」，填写私聊 QQ、选择发送内容；测试只读缓存与配置。

> 旧版 `config.toml` 里的 `send_time`、`send_every_days`、`timezone`、`send_format` 会被忽略，不需要手动删除；新配置保存后自然消失。

## 配置项

| 字段 | 默认 | 范围 / 取值 | 说明 |
| --- | --- | --- | --- |
| `send_enabled` | `true` | 开关 | 关闭后静音：新闻指令不拉取、不发送；`RSS列表`/`设置昵称` 仍可用 |
| `content_format` | `title_link_lead` | 五种 | 仅标题 / 标题和链接 / 标题和开头 / 标题和正文 / 标题、链接和开头 |
| `per_source_limit` | `10` | 1–100 | 每条订阅每次请求最多发送篇数 |
| `per_destination_limit` | `50` | 1–500 | 单次请求用户最多接收的文章总数（面板标题为“用户接收文章最大数量”） |
| `pull_interval_minutes` | `120` | 15–720 | 同一个源两次拉取的最小间隔 |
| `cache_days` | `10` | 7–30 | 文章保留天数；超期文章在请求/启动时立即删除 |
| `message_interval_seconds` | `2` | 1–5 | “所有RSS新闻”多条消息之间的间隔 |

当「订阅数 × 每订阅上限」大于「用户接收文章最大数量」时，插件页面会提示饿死风险；添加/修改订阅时也会在响应里返回该警告。

## 指令

| 指令 | 权限 | 私聊 | 群聊 | 行为 |
| --- | --- | --- | --- | --- |
| `/RSS新闻 [源名称/简称]` | 所有人 | ✅ | ❌ 返回仅私聊 | 一个源，最多 `per_source_limit` 篇；无参数用权重最小的源 |
| `/所有RSS新闻` | 所有人 | ✅ | ❌ 返回仅私聊 | 所有到期源按权重顺序逐订阅发送，受总数上限限制 |
| `/RSS列表` | 所有人 | ✅ | ✅ | 返回“源名称（源简称）”；简称为空时只返回名称 |
| `/设置昵称 订阅全称 [订阅简称]` | 次级管理员 | ✅ | ✅ | 设置简称；省略简称即清空；重复则拒绝 |

`/RSS新闻` 只接受一个源：参数中给出多个源名称时拒绝且不拉取、不发送；名称有误同理。简称和全称都可匹配。

## 拉取与发送细节

- 每个源独立记录 `pull_at`。请求指定源时只检查该源；`/所有RSS新闻` 检查全部源，只拉取到期的源。
- 拉取失败不会阻断发送：会继续用已有缓存；缓存为空时回复“暂时没有可发送的文章。”。
- 每次请求先删除超过 `cache_days` 的文章，再读取缓存。
- `/所有RSS新闻` 先按权重选文章（每源最多 `per_source_limit`，总数最多 `per_destination_limit`），再把每个订阅的文章合并成消息发送；每条消息以 `源名称（源简称）：` 开头，超过 3950 字自动拆成多条（每条都带该开头），间隔使用配置值。
- 文章开头按 200 个 Unicode 字符截断并加省略号；单条消息最多 3950 字。
- 不再有定时器、`next_send`、发送窗口、接收者表、已发送表或失败计数。

## 数据与旧库迁移

数据库路径：`plugins_data/NeoBot_RSS_News/databases/rss_news.db`。

打开数据库时会自动迁移到版本 2：

- 删除 `destinations`（接收者）和 `sent`（已发送）表（如存在）。
- `articles` 表移除 `deleted_at`，已标记删除的文章直接丢弃；超期文章由清理逻辑立即 `DELETE`。
- `subscriptions` 增加 `nickname TEXT`；非空简称有唯一索引，空简称可重复。
- 为现有预设补上“中新网 / 之家”简称；用户清空后不会在下次启动补回。
- 新表结构：`subscriptions(id, name, url, nickname, weight, created_at, pull_at)`、`articles(source_id, guid, title, link, body, summary, pubdate, fetched_at)`，文章索引为 `(source_id, pubdate DESC)`。

迁移失败会回滚并阻止插件启动；请先备份 `databases/rss_news.db`。

## 开发与验收

在插件目录执行，假设 NeoBot 源码与插件同级：

```sh
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk check .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export . --check
uv run --project ../NeoBot --no-sync python ../NeoBot/scripts/plugin_ci.py . --output-dir ../NeoBot_RSS_News_acceptance
```

本次实际验收：`plugin_ci` 全部阶段通过，`114 passed`，两次构建 SHA256 一致，ZIP 安装、启动、停止、重启、重载与外部配置冒烟通过。

测试覆盖：配置、昵称唯一/清空、旧库迁移、按需拉取与并发上限、`/RSS新闻` 私聊/群聊/多源/未知源、`/所有RSS新闻` 顺序与限额、`/RSS列表`、`/设置昵称` 权限与重复、3950/200 字规则、SSRF、feedparser 重试、只读发送测试。

## QuailEgg 发版

工程包含 `.quailegg.toml`、`.changes/` 与 `.github/workflows/qegg-*.yml`。发布前把仓库地址改成实际地址（当前配置为 `ABPolaris/NeoBot_RSS_News`），并在有 `gh` 的环境执行 `qegg check`。插件包默认 `publish = false`。

## 故障排查

| 现象                     | 处理                                              |
| ---------------------- | ----------------------------------------------- |
| 新闻指令回复“新闻仅支持在私聊中查看。”   | 这是预期行为；请在私聊中发送新闻指令                              |
| 私聊一直不发送                | 检查配置“允许发送新闻”是否打开；确认订阅有缓存或允许拉取                   |
| 刚升级后不发送                | 旧 `send_enabled=false` 会被当作静音，到配置表单打开“允许发送新闻”   |
| 群聊中 `/设置昵称` 没反应        | 需要次级管理员以上权限；权限由宿主命令系统检查                         |
| 简称重复                   | 修改被拒绝，回复会指出被哪个订阅占用；先清空或改用其它简称                   |
| 数据库升级失败                | 备份后查看插件错误；迁移失败会回滚，不会静默丢数据                       |
| 页面提示“插件没有声明此操作”        | 页面资源比 Python 后端新；在插件管理中重载插件或重启 NeoBot           |
| 日志大量 `aiosqlite` DEBUG | 宿主 DEBUG 日志会把一条 SQL 拆成多行；调到 INFO 可隐藏，且本插件已无定时轮询 |

完整 API 见 NeoBot 的 `docs/07-插件开发.md`、`docs/04-功能文档/插件SDK与工程验收.md`、`docs/04-功能文档/插件设置界面.md`。
