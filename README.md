# 小工具生成器 (Mini-Tool Generator)

一个简化版 Atom 平台：用户输入一句话需求，后端调用大模型生成单文件 HTML 工具页面，前端用 iframe 实时渲染。

## 技术栈

- **后端**: Python + FastAPI
- **前端**: 原生 HTML / CSS / JS（无构建工具）
- **持久化**: Supabase
- **大模型**: OpenAI 兼容接口（可切换硅基流动、DeepSeek 等）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制环境变量模板
copy .env.example .env
# 然后编辑 .env 填入真实密钥（留空也能跑——会进入 mock 模式）

# 3. 启动服务
python main.py
# 或
uvicorn main:app --host 0.0.0.0 --port 8000
```

打开浏览器访问 `http://localhost:8000` 即可。

## 目录结构

```
.
├── main.py              # FastAPI 入口（含 generate_app 函数 + mock 示例）
├── static/
│   └── index.html       # 前端单页应用
├── requirements.txt
├── .env.example
└── README.md
```

## 核心 API

### POST /api/generate

请求体:
```json
{ "prompt": "一个能算房贷月供、带提前还款对比的计算器" }
```

响应:
```json
{ "html": "<!DOCTYPE html>...</html>", "mode": "mock | llm" }
```

## Mock 模式

当检测不到 `LLM_API_KEY` 或大模型调用失败时，自动返回 3 个预置示例之一：

1. **房贷计算器** — 真正可用，含月供计算 + 提前还款对比
2. **待办事项清单** — 本地存储的 Todo App
3. **JSON 格式化工具** — 美化 / 压缩 / 校验 JSON

## 替换真实大模型

只需修改 `main.py` 里的 `generate_app(prompt)` 函数实现，函数签名保持不变即可：

```python
def generate_app(prompt: str) -> str:
    # TODO: 你的大模型调用逻辑
    ...
```

## 环境变量

| 变量 | 说明 | 必须 |
|------|------|------|
| `PORT` | 服务监听端口，默认 `8000` | 否 |
| `LLM_API_KEY` | 大模型 API Key | 否（无则走 mock） |
| `LLM_BASE_URL` | 大模型 Base URL | 否 |
| `LLM_MODEL` | 模型名 | 否 |
| `SUPABASE_URL` | Supabase 项目 URL | 否 |
| `SUPABASE_ANON_KEY` | Supabase 匿名 Key | 否 |

## License

MIT
