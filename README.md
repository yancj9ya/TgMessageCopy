# TgMessageCopy

一个纯 Python 的 Telegram 频道消息转发项目，已按模块拆分，便于维护和扩展。

## 功能特性

- 监听指定 Telegram 来源频道的新消息
- 转发到另一个 Telegram 频道
- 优先使用原生转发，失败后自动降级为重新发送
- 支持文本、图片、视频、文件
- 支持关键词黑名单 / 白名单过滤
- 支持正则黑名单 / 白名单过滤
- 首次启动时自动读取来源频道最新消息 ID，避免从 0 开始的状态语义不清晰
- 使用 `data/state.json` 持久化保存每组转发规则的最后处理位置
- 输出控制台日志，同时写入 [`data/logs/tgmsgcopy.log`](data/logs/tgmsgcopy.log)
- 支持通过 Telegram 管理 Bot 在线查看配置、增删规则、重载转发器
- 接收消息与转发消息已拆分为生产者-消费者模型：接收链路负责过滤入队，后台消费者负责串行发送

## 安装

建议使用 Python 3.10 及以上版本。

安装依赖：

```cmd
pip install -r requirements.txt
```

## Docker 部署

### 构建镜像

```cmd
docker build -t tgmessagecopy .
```

### 运行容器

```cmd
docker run -d --name tgmessagecopy -v %cd%\data:/app/data tgmessagecopy
```

说明：
- 容器内工作目录为 `/app`
- 持久化目录挂载为 `/app/data`
- 所有配置、状态、队列、日志、session 都保存在挂载卷中

### 首次初始化配置

首次运行后，如果 [`data/config.toml`](data/config.toml) 不存在，容器会自动生成模板。

你需要编辑宿主机上的：
- [`data/config.toml`](data/config.toml)

然后重启容器：

```cmd
docker restart tgmessagecopy
```

## GitHub Actions 自动构建

已提供工作流文件：[`docker-build.yml`](.github/workflows/docker-build.yml)

触发条件：
- push 到 `main`
- push 到 `master`
- 手动触发 `workflow_dispatch`

当前行为：
- 自动检出代码
- 自动执行 Docker Buildx
- 自动构建 [`Dockerfile`](Dockerfile)
- 默认只做构建校验，不推送镜像

## 项目结构

- [`tgmsgcopy.py`](tgmsgcopy.py:1)：程序入口
- [`tgforwarder/config.py`](tgforwarder/config.py:1)：配置模板、配置加载、配置校验、代理解析
- [`tgforwarder/logging_config.py`](tgforwarder/logging_config.py:1)：日志初始化
- [`tgforwarder/state.py`](tgforwarder/state.py:1)：状态文件读写
- [`tgforwarder/filters.py`](tgforwarder/filters.py:1)：关键词 / 正则过滤逻辑
- [`tgforwarder/forwarder.py`](tgforwarder/forwarder.py:1)：Telegram 监听、转发、媒体降级发送主逻辑
- [`tgforwarder/paths.py`](tgforwarder/paths.py:1)：路径常量
- [`tgforwarder/config_store.py`](tgforwarder/config_store.py:1)：配置持久化读写服务
- [`tgforwarder/runtime.py`](tgforwarder/runtime.py:1)：转发器运行时与重载管理
- [`tgforwarder/bot_manager.py`](tgforwarder/bot_manager.py:1)：Telegram 管理 Bot

## 持久化目录

程序会自动创建统一持久化目录 [`data/`](data)，用于存放配置、Telethon session、状态文件、日志和临时下载文件。

- [`data/config.toml`](data/config.toml)：配置文件
- [`data/state.json`](data/state.json)：转发进度状态
- [`data/queue.json`](data/queue.json)：持久化待转发队列
- [`data/logs/tgmsgcopy.log`](data/logs/tgmsgcopy.log)：日志文件
- [`data/sessions/`](data/sessions)：Telethon session 持久化目录
- [`data/downloads/`](data/downloads)：媒体临时下载目录

这样做的好处是：项目代码和运行数据分离，后续迁移、备份、部署会更清晰。

## 启动

```cmd
python tgmsgcopy.py
```

首次运行时，如果 [`data/config.toml`](data/config.toml) 不存在，程序会自动生成带注释的模板文件并退出。

你需要填写配置后重新运行。

## 配置说明

配置文件路径：[`data/config.toml`](data/config.toml)

示例：

```toml
# Telegram API ID，可从 https://my.telegram.org 获取
api_id = 123456

# Telegram API Hash，可从 https://my.telegram.org 获取
api_hash = "your_api_hash"

# Telethon session 名称，实际 session 文件会保存在 data/sessions/ 目录
session_name = "session_name"

# 代理配置；不使用代理时保留为空字符串
proxy = ""

# 管理 Bot 的 token
bot_token = ""

# 允许操作管理 Bot 的 Telegram 用户 ID 列表
admin_user_ids = [123456789]

[[targets]]
# 来源频道用户名或频道 ID
source = "source_channel_username_or_id"

# 目标频道用户名或频道 ID
destination = "target_channel_username_or_id"

# 首次启动处理的起始消息 ID，0 表示自动从当前最新消息开始
startup_last_message_id = 0

# 黑名单关键词，命中即跳过
blacklist_keywords = []

# 白名单关键词，非空时必须命中其一才转发
whitelist_keywords = []

# 黑名单正则，命中即跳过
blacklist_regex = []

# 白名单正则，非空时必须命中其一才转发
whitelist_regex = []

# 原生转发失败时，重发消息附加的前缀
caption_prefix = ""
```

### 顶层字段

- `api_id`：Telegram API ID
- `api_hash`：Telegram API Hash
- `session_name`：Telethon session 名称，实际文件会持久化到 [`data/sessions/`](data/sessions)
- `proxy`：可选代理配置，传 Telethon 支持的 tuple/list 格式，例如 `[2, "127.0.0.1", 7890]`
- `bot_token`：管理 Bot 的 token
- `admin_user_ids`：允许操作管理 Bot 的 Telegram 用户 ID 列表
- `targets`：转发规则列表

### targets 字段

- `source`：来源频道，支持以下格式：`@username`、`username`、`https://t.me/xxx`、数值 ID（如 `-1001234567890`）
- `destination`：目标频道，支持以下格式：`@username`、`username`、`https://t.me/xxx`、数值 ID（如 `-1001234567890`）
- `startup_last_message_id`：
  - 填数字：程序首次启动时从该消息 ID 之后开始处理
  - 填 `null`：程序首次启动时自动读取来源频道当前最新消息 ID，并从后续新消息开始监听
- `blacklist_keywords`：关键词黑名单，命中即跳过
- `whitelist_keywords`：关键词白名单，非空时必须命中其中之一才转发
- `blacklist_regex`：正则黑名单，命中即跳过
- `whitelist_regex`：正则白名单，非空时必须命中其中之一才转发
- `caption_prefix`：当原生转发失败、降级为重新发送时，追加到正文前面的前缀

## 运行产物

- [`tgmsgcopy.py`](tgmsgcopy.py:1)：主入口
- [`requirements.txt`](requirements.txt)：依赖列表
- [`data/config.toml`](data/config.toml)：运行配置
- [`data/state.json`](data/state.json)：转发进度状态
- [`data/logs/tgmsgcopy.log`](data/logs/tgmsgcopy.log)：日志文件
- [`data/sessions/`](data/sessions)：Telethon session 持久化目录
- [`data/downloads/`](data/downloads)：媒体重发时的临时下载目录

## 转发逻辑说明

1. 收到来源频道新消息
2. 根据关键词和正则规则判断是否允许进入待转发队列
3. 生产者将消息放入内存队列
4. 同时将最小必要字段落盘到 [`data/queue.json`](data/queue.json)
5. 后台消费者从队列中串行取出消息并执行发送
6. 程序启动时会从 [`data/queue.json`](data/queue.json) 恢复待发消息，并根据 `message_id` 重新拉取原消息后继续发送
7. 优先尝试 Telegram 原生转发
8. 如果原生转发失败，则按规则切换到本轮运行期内的强制重发模式
9. 在消息成功发送或被确认跳过后，更新 [`data/state.json`](data/state.json) 中对应规则的最后消息 ID，并从 [`data/queue.json`](data/queue.json) 中移除

## 管理 Bot 命令

在配置好 `bot_token` 和 `admin_user_ids` 后，程序会同时启动管理 Bot。

当前支持命令：

- `/start`
- `/help`
- `/status`
- `/targets`
- `/reload`
- `/add_target`
- `/remove_target <index>`
- `/set_blacklist <index> <v1,v2,...>`
- `/set_whitelist <index> <v1,v2,...>`
- `/set_black_regex <index> <r1,r2,...>`
- `/set_white_regex <index> <r1,r2,...>`
- `/cancel`

说明：
- 只有 `admin_user_ids` 中的用户可以操作
- `/add_target` 现在会进入对话式引导，依次询问：`source`、`destination`、关键词黑白名单、正则黑白名单、`caption_prefix`
- `/status` 现在会显示：运行状态、规则总数、启用/停用数量、内存待转发队列数量、持久化队列数量，以及当前处于“强制重发模式”的规则列表
- 在对话式添加过程中，发送 `/cancel` 可以取消当前流程
- `/targets` 会附带按钮菜单，可直接点选某条规则查看其编辑提示
- 在规则详情页中，还可以通过按钮直接进入字段编辑：黑名单关键词、白名单关键词、黑名单正则、白名单正则、`caption_prefix`
- 规则详情页还支持按钮直接修改 `source`、`destination`，以及“删除此规则”
- 规则详情页支持“返回规则列表”按钮
- 规则支持启用 / 停用状态，详情页可直接通过按钮切换启停
- 过滤配置命令通过规则序号直接更新指定 target 的过滤字段
- `add_target` / `remove_target` 会自动更新 [`data/config.toml`](data/config.toml)
- 配置修改后会自动触发转发器热重载

## 注意事项

- 你的 Telegram 账号必须能访问来源频道，并且有权限向目标频道发消息
- 如果目标频道禁止普通成员转发，原生转发可能失败，程序会自动走重发兜底逻辑
- 如果用了正则过滤，请确保表达式写法正确
- 首次登录 Telethon 时，终端可能要求输入验证码或两步验证密码

## 后续可扩展方向

- 支持历史消息补发
- 支持更细的媒体类型过滤
- 支持多级日志轮转
- 支持 Docker 部署
