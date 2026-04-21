# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

- 安装依赖：`pip install -r requirements.txt`（需要 Python 3.10+，Docker 镜像使用 3.13-slim）
- 本地运行：`python tgmsgcopy.py`
- 构建镜像：`docker build -t tgmessagecopy .`
- 本地 compose：`docker compose up -d` / `docker compose down`（默认直接拉取 `ghcr.io/yancj9ya/tgmessagecopy`）

仓库未配置任何测试框架或 lint 工具，`requirements.txt` 仅声明运行时依赖（`telethon==1.43.1`、`python-telegram-bot==21.10`）。不要虚构 `pytest`/`ruff` 等命令。

## 首次启动行为

`ensure_config()` 在 `data/config.toml` 不存在时会写入模板并通过 `SystemExit` 退出 —— 这是**预期行为**，不是 bug。必须让用户填充 `data/config.toml` 后才能正常启动。`data/` 目录同时出现在 `.gitignore` 和 `.dockerignore` 中，所有运行时产物（session、state、queue、logs、downloads）都落在这里。

## 高层架构

入口 `tgmsgcopy.py` 按固定顺序装配三个核心对象：

1. `ConfigStore`（`tgforwarder/config_store.py`）—— TOML 读写，包含自定义 `dump_config_toml` 以保留中文注释；所有配置写回都走 `validate_config`。
2. `ForwarderRuntime`（`tgforwarder/runtime.py`）—— 用 `asyncio.Task` 包裹 `run_forwarder`，`reload()` 是 `stop → start` 的组合（持 `_reload_lock` 串行）；状态字段挂在 `config["_runtime_state"]` 上以便 Bot 侧查询。
3. `BotManager`（`tgforwarder/bot_manager.py`）—— 基于 `python-telegram-bot` 的管理 Bot，命令修改配置后调用 `runtime.reload()` 实现热重载。

### 转发流水线（`forwarder.run_forwarder`）

采用**生产者-消费者**模型，避免 Telethon 事件回调内同步发送造成阻塞：

- 生产者：`@client.on(events.NewMessage)` handler 过滤 `message.id <= state[key]` 后，同时入队到内存 `asyncio.Queue` 与 `QueueStore`（`data/queue.json`）。
- 消费者：`consumer_loop` 串行 `queue.get()` → `process_message()` → 成功后 `save_state()` + `queue_store.remove_first_match()`。`state.json` 与 `queue.json` 的一致性仅由"先状态后队列"的顺序保证。
- 启动时从 `queue.json` 恢复未处理项，通过 `client.get_messages(..., ids=...)` 重新拉取原消息对象后入队。

### 原生转发降级

`safe_forward_message` 优先 `client.forward_messages`，遇到 `ChatAdminRequiredError` / `RPCError` 会把 `state_key` 加入 `native_forward_disabled`（一个 `set`），本轮运行期内该规则**只走**"下载媒体 + `send_file` + `caption_prefix`"的重发路径，直到下次 `reload()` 清空集合。`FloodWaitError` 不触发降级，仅 `asyncio.sleep(exc.seconds)` 后重试。

### 过滤范围

`filters.extract_searchable_message_text` 把以下内容拼成单一字符串再跑关键词/正则：正文、`MessageEntityTextUrl.url`、`MessageEntityUrl` 覆盖的正文切片、inline keyboard 按钮的 text 与 url。修改过滤逻辑时要同时考虑这四个来源。黑名单命中即拒；白名单非空时必须命中其一；关键词大小写不敏感（`normalize_text` + `lower()`），正则使用 `re.IGNORECASE | re.DOTALL`。

### 状态键

`state.build_target_key(source, destination)` → `"{source}=>{destination}"`。若 `source` 或 `destination` 在配置中被改写（例如由 `@username` 改为数字 ID），旧 state key 会残留在 `state.json` 中 —— 这是已知行为，不要误以为是 bug。

### 频道标识规范化

`chat_format.normalize_chat_input` 统一接受 `@username`、`username`、`https://t.me/xxx`、`https://telegram.me/xxx`、纯数字 ID（含负数）；Bot 命令输入与配置读取都要经过它。新增需要接受频道输入的代码路径时必须复用这个函数而不是自己解析。

## 部署

GitHub Actions (`.github/workflows/docker-build.yml`) 在 push 到 `main`/`master` 或手动触发时，通过 Buildx 构建并推送 `ghcr.io/${owner}/tgmessagecopy:latest` 以及 `:sha-<commit>` 到 GHCR，使用内置 `GITHUB_TOKEN`。`docker-compose.yml` 中的镜像 tag 是硬编码的 sha（例如 `sha-0a47f78`），发布新版本时需要手动更新。
