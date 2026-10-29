# 小工具生成器（Mini-Tool Generator）

一个**简化版 AI Agent 平台**：一句话需求 → 生成可直接运行的网页小工具，支持**赛马择优**（3 路并发生成、并排对比、选最优）与**连续修改**（在原工具基础上迭代），生成结果持久化到 Supabase。

## 功能特性

| 能力 | 说明 |
|------|------|
| 一句话生成 | 输入自然语言需求，调用大模型生成**单文件 HTML**（CSS/JS 全内联、无外部依赖），iframe 中即时渲染 |
| 赛马择优 | 同一需求并发 3 路生成（简洁实用 / 美观现代 / 创意大胆，温度 0.4 / 0.7 / 1.0），三栏并排对比，「就用这个」选定最终版 |
| 连续修改 | 打开任意历史项目，输入修改要求，模型在**原 HTML 上做最小改动**，新版本写回同一项目 |
| 历史持久化 | 每次生成 / 赛马 / 修改都写入 Supabase（`projects` + `messages`），刷新页面、换浏览器后历史仍在 |
| 健壮性兜底 | 大模型调用统一超时 + 重试 2 次；无 Key / 额度耗尽 / 网络抖动自动回退 mock；全局异常统一返回可读 JSON；前端全程 loading + 错误提示，不白屏 |

## 技术栈分析

依赖清单：[requirements.txt](file:///d:/AI-trae/atoms-toolgen/requirements.txt)（本项目为 Python 后端，无 `package.json`、无前端构建工具）

| 层次 | 技术 | 版本 / 说明 |
|------|------|-------------|
| 语言 | Python | 3.10+（线上 3.11） |
| Web 框架 | FastAPI | `0.115.0` |
| ASGI 服务器 | Uvicorn | `0.30.6`（`python -m uvicorn main:app`） |
| HTTP 客户端 | httpx | `0.27.2`（调用大模型 + Supabase REST） |
| 环境变量 | python-dotenv | `1.0.1`（启动时加载 `.env`） |
| 前端 | 原生 HTML / CSS / JavaScript | 单文件 [static/index.html](file:///d:/AI-trae/atoms-toolgen/static/index.html)，无框架、无打包 |
| 大模型 | OpenAI 兼容 `POST /chat/completions` | 本项目当前配置 DeepSeek（`https://api.deepseek.com` / `deepseek-flash`），可换成 OpenAI、硅基流动等 |
| 持久化 | Supabase（PostgREST） | 后端用 service_role key 访问，未配置时自动降级 |
| 部署 | 阿里云服务器 + systemd（端口 8000） | 备选 [render.yaml](file:///d:/AI-trae/atoms-toolgen/render.yaml)，见下文「部署 / 公网访问」 |

## 目录结构与模块职责

```
atoms-toolgen/
├── main.py                 # 后端入口：配置、Mock 示例、大模型调用、赛马、全部 API、异常兜底
├── db.py                   # Supabase 持久化模块（PostgREST 封装，未配置时优雅降级）
├── static/
│   └── index.html          # 前端单页应用（样式 + 页面结构 + 内联 JS）
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量模板（复制为 .env 后填真实值）
├── render.yaml             # Render 部署配置
├── .gitignore              # 忽略 .env / __pycache__ / *.log 等
└── README.md
```

### [main.py](file:///d:/AI-trae/atoms-toolgen/main.py)（约 1400 行，按职责分块）

| 区块 | 职责 |
|------|------|
| 配置读取（约 L40-75） | 从环境变量读 `PORT`、`LLM_*`、超时/重试参数；占位符 Key 视为未配置 |
| Mock 示例（约 L80-807） | 3 个可直接运行的预置网页（房贷计算器 / 待办清单 / JSON 格式化），大模型不可用时兜底 |
| 大模型调用（约 L820-1000） | 构造 prompt、清洗模型输出（去掉说明文字与 Markdown 代码围栏）、超时 + 指数退避重试、状态码转中文原因 |
| 生成与赛马（约 L1001-1156） | `generate_app`（单路，返回 html/mode/notice）、`_call_llm_iterate`（连续修改）、`generate_race`（3 路 `asyncio.gather` 并发、单路失败隔离） |
| FastAPI 应用与异常兜底（约 L1159-1205） | 统一异常处理器：HTTP 异常 / 参数校验 / 未捕获异常 → `{error, status, message}` JSON |
| 路由（约 L1207-1399） | `/api/generate`、`/api/generate_race`、`/api/projects*`、`/api/health`、`/` 首页 |
| 启动入口（L1404-1411） | `python main.py` 时打印配置摘要并启动 uvicorn |

### [db.py](file:///d:/AI-trae/atoms-toolgen/db.py)

- 只用标准库 + httpx 直连 Supabase REST（`/rest/v1/projects`、`/rest/v1/messages`），不引入 SDK。
- 提供 `insert_project` / `list_projects` / `get_project` / `update_project_html` / `insert_message` / `list_messages`。
- `SUPABASE_URL` 或 `SUPABASE_SERVICE_KEY` 缺失（或为占位符）时 `ENABLED=False`，所有函数返回 `None`/`False`/`[]`，**不影响生成与赛马主流程**，只禁用历史与连续修改。

### [static/index.html](file:///d:/AI-trae/atoms-toolgen/static/index.html)

单文件 SPA，三段式：`<style>` 样式 → `<body>` 页面结构（顶栏 / 输入区 / 预览区 / 赛马三栏 / 历史侧栏 / 修改条 / loading / toast） → 末尾内联 `<script>` 前端逻辑。

| 函数 | 职责 |
|------|------|
| `checkHealth` | 读取 `/api/health`，展示「LLM 模式 / Mock 模式」与 Supabase 状态 |
| `generate` / `generateSingle` | 单路生成，带 loading 与错误提示，mock 降级时提示原因 |
| `generateRace` / `renderRace` / `pickVariant` | 赛马三栏渲染、失败路占位、「就用这个」选定并回填历史记录 |
| `loadProjects` / `openProject` | 历史列表加载与重开（含未选定的赛马记录还原三栏） |
| `iterate` | 连续修改当前项目 |
| `renderInIframe` / `refreshIframe` / `openInNewTab` / `copyHtml` | Blob URL 渲染、刷新、新窗口打开、复制 HTML |
| `showToast` / `showLoading` / `errorMessage` | 统一提示、loading 遮罩、解析后端错误 |
| 全局兜底（文件末尾） | `unhandledrejection` / `error` 监听，任何未捕获异常只弹 toast，页面不白屏 |

## 本地启动指南

```bash
# 1. 克隆代码（Windows PowerShell）
git clone https://github.com/xueerhuan2014/atoms-toolgen.git
cd atoms-toolgen

# 2. 创建并激活虚拟环境（可选，推荐）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装依赖
pip install -r requirements.txt

# 4. 复制环境变量模板
Copy-Item .env.example .env

# 5. 编辑 .env，填入 LLM_API_KEY / SUPABASE_*（全部留空也能启动，进入 mock 模式）

# 6. 启动服务（二选一）
python -m uvicorn main:app --host 0.0.0.0 --port 8000
# 或：python main.py
```

访问 <http://localhost:8000> 打开页面；<http://localhost:8000/api/health> 查看当前模式：

```json
{ "status": "ok", "llm_configured": true, "supabase_configured": true, "mock_examples": ["房贷计算器", "待办事项清单", "JSON 格式化工具"] }
```

> 修改 `.env` 后需重启服务才生效。

## 环境变量说明

模板见 [.env.example](file:///d:/AI-trae/atoms-toolgen/.env.example)，真实值只放 `.env`（已被 `.gitignore` 忽略，切勿提交）。

| 变量 | 必填 | 默认值 | 说明 / 获取方式 |
|------|------|--------|----------------|
| `PORT` | 否 | `8000` | 服务监听端口；Render 会自动注入 |
| `LLM_API_KEY` | 否 | 空 | 大模型 API Key。留空或填占位符 → 自动走 mock 模式。DeepSeek：<https://platform.deepseek.com/api_keys>；OpenAI：<https://platform.openai.com/api-keys>；硅基流动：<https://cloud.siliconflow.cn/account/ak> |
| `LLM_BASE_URL` | 否 | `https://api.openai.com/v1` | OpenAI 兼容接口地址。DeepSeek 用 `https://api.deepseek.com`，OpenAI 用 `https://api.openai.com/v1` |
| `LLM_MODEL` | 否 | `gpt-4o-mini` | 模型名。DeepSeek 常用 `deepseek-flash` |
| `LLM_TIMEOUT` | 否 | `60` | 单路生成单次请求超时（秒） |
| `LLM_TIMEOUT_ITERATE` | 否 | `90` | 连续修改单次请求超时（秒，输入更长） |
| `LLM_MAX_RETRIES` | 否 | `2` | 失败后重试次数（仅对超时 / 408 / 429 / 5xx 重试，401/403 不重试） |
| `LLM_RETRY_BACKOFF` | 否 | `1.5` | 重试退避基数（秒），第 n 次重试等待 `1.5 × n` 秒 |
| `DAILY_LLM_BUDGET` | 否 | `40` | 全站每日大模型调用次数上限（生成 / 修改各算 1 次，赛马三路算 3 次），超出返回 429；防止公开链接被刷 token |
| `DAILY_IP_BUDGET` | 否 | `40` | 单个 IP 每日调用次数上限，超出返回 429 |
| `SUPABASE_URL` | 否 | 空 | Supabase 项目地址：控制台 → Project Settings → Data API → Project URL。**不要带 `/rest/v1` 后缀**（代码会自动兼容） |
| `SUPABASE_SERVICE_KEY` | 否 | 空 | Supabase 控制台 → Project Settings → API Keys → `service_role`（secret）。**不要用 anon key**，且只能放在后端 |

未配置 Supabase 时：生成与赛马照常可用，历史列表 / 打开项目 / 连续修改接口返回 503 与可读提示（前端弹 toast）。

## 核心 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/generate` | 单路生成。请求 `{"prompt": "..."}`；响应 `{html, mode, prompt, project_id, notice}`（`mode` 为 `llm`/`mock`，`notice` 为回退原因） |
| POST | `/api/generate_race` | 赛马：并发 3 路，响应 `{prompt, mode:"race", ok_count, project_id, results:[{label,ok,html,error}]}` |
| POST | `/api/projects` | 赛马选定后回填（带 `project_id`）或首次落库 |
| GET | `/api/projects` | 历史列表（不含大字段，倒序） |
| GET | `/api/projects/{id}` | 项目完整内容 + 对话消息；id 不存在返回 404「项目不存在」 |
| POST | `/api/projects/{id}/iterate` | 连续修改。请求 `{"prompt":"..."}`；失败返回 502，原版本不受影响 |
| GET | `/api/health` | 健康检查 + 当前模式（`llm_configured` / `supabase_configured`） |

所有接口出错都返回统一 JSON：`{"error": true, "status": 4xx/5xx, "message": "中文可读原因"}`。

## Supabase 建表（可选）

在 Supabase 控制台 → SQL Editor 执行（启用 RLS，service_role 会绕过，anon 无法直接读写）：

```sql
create table if not exists projects (
  id uuid primary key default gen_random_uuid(),
  prompt text not null,
  final_html text,
  race_versions jsonb,
  created_at timestamptz not null default now()
);

create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  role text not null,
  content text not null,
  created_at timestamptz not null default now()
);

alter table projects enable row level security;
alter table messages enable row level security;
```

执行后应提示 `Success. No rows returned`，再到 Table Editor 确认两张表存在。

## 降级与容错行为

| 情况 | 表现 |
|------|------|
| 未配置 / 占位 Key | 直接返回预置 mock 示例，`notice = 未配置 API Key`，前端 toast 提示 |
| Key 无效（401/403） | 不重试，立即回退 mock，`notice = API Key 无效或没有权限` |
| 额度耗尽 / 限流（429） | 重试 2 次后回退 mock，`notice` 说明限流或额度耗尽 |
| 网络抖动 / 5xx | 每次请求独立超时，退避重试 2 次后回退 mock，接口仍返回 200 |
| 赛马单路失败 | 该栏显示失败原因，其余路正常渲染，可正常选定 |
| 修改失败 | 返回 502 可读提示，原版本内容不变 |
| 任意接口异常 | 全局异常处理器返回可读 JSON；前端 toast 提示，不白屏 |

最坏耗时：单路生成约 `3 × 60s + 退避 ≈ 3 分钟`；连续修改约 `3 × 90s`；赛马三路并发，与单路同量级。

## 部署 / 公网访问

**当前线上地址：<http://8.221.136.192:8000>**（阿里云服务器，`systemd` 托管，开机自启、进程异常自动拉起）

### 方案 A：服务器常驻部署（当前采用）

代码位于 `/opt/atoms-toolgen`，使用 Python 3.11 虚拟环境，`uvicorn` 监听 `0.0.0.0:8000`，由 `atoms-toolgen.service` 托管：

```ini
[Unit]
Description=Atoms Tool Generator
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/atoms-toolgen
ExecStart=/opt/atoms-toolgen/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

常用命令：

```bash
systemctl status atoms-toolgen     # 查看状态
systemctl restart atoms-toolgen    # 更新代码后重启
journalctl -u atoms-toolgen -f     # 实时日志
```

首次部署：上传代码与 `.env` → `python3.11 -m venv .venv` → `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` → 写入上面的 unit 文件 → `systemctl daemon-reload && systemctl enable --now atoms-toolgen`。云服务器需在安全组放行 TCP 8000。

### 方案 B：Cloudflare Tunnel（备用 —— 无需服务器）

前提：本地服务已在运行（见上文「本地启动指南」）。开一个**新的** PowerShell 窗口：

```powershell
# 1. 安装 cloudflared（只需一次）
winget install Cloudflare.cloudflared

# 2. 建立临时公网隧道（指向本地 8000 端口）
cloudflared tunnel --url http://localhost:8000
```

终端会打印一行 `https://xxxx-xxxx-xxxx.trycloudflare.com`，这就是可直接访问的公网地址（`/` 是页面，`/api/health` 可用于自检）。

注意：

- 快速隧道没有 SLA，**关闭终端 / 关闭电脑 / 重启服务后链接即失效**，下次启动会生成新链接；
- 演示期间电脑需保持开机且不休眠；
- 链接是公开的：拿到链接的人都能使用你的大模型额度、查看历史记录，演示结束请 `Ctrl+C` 关闭隧道；
- 若提示 `cloudflared` 不是内部或外部命令，重新打开 PowerShell 让 PATH 生效，或直接用完整路径 `& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --url http://localhost:8000`。

### 方案 C：Render

仓库自带 [render.yaml](file:///d:/AI-trae/atoms-toolgen/render.yaml)：`pip install -r requirements.txt` + `uvicorn main:app --host 0.0.0.0 --port ${PORT}`，健康检查路径 `/api/health`。环境变量在 Render 控制台 Environment 中单独填写，不要写进仓库。

> 注意：Render 免费实例创建 Web Service 时需要绑定银行卡；若无法绑卡，请使用方案 B。

## 常见问题

- **启动报 `WinError 10048`（端口占用）**：先停掉占用 8000 的进程，或改用其他端口（`python -m uvicorn main:app --port 8001`）。
- **一直显示 Mock 模式**：检查 `/api/health` 的 `llm_configured`；若为 `false`，确认 `.env` 里 `LLM_API_KEY` 不是占位符，且修改后已重启。
- **历史列表为空 / 接口 503**：Supabase 未配置或表未创建，按上文「Supabase 建表」操作。
- **History 出现重复记录**：同一需求多次生成会各存一条，属预期行为。

## License

MIT
