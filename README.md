# 量化监测-股票

准实时股票监测、行业资讯聚合与 AI 归因分析工具。项目只分析局势、影响、风险和证据，不提供自动交易、买卖指令或收益承诺。

## 项目结构

- `stock-monitor/`：Next.js 16 前端。
- `backend/`：FastAPI 后端、公开数据抓取、AI 分析、定时任务和 SQLite 运行数据。
- `AGENTS.md`：项目开发与协作规则。

## 环境要求

- Node.js 20.19 或更高版本。
- Python 3.9 或更高版本。
- npm。

## 环境变量

复制模板后填写本地真实值：

```bash
cp backend/.env.example backend/.env
cp stock-monitor/.env.example stock-monitor/.env.local
```

真实 `.env`、数据库和日志均属于本地运行数据，不得提交到 Git。

## 安装依赖

后端：

```bash
cd backend
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

前端：

```bash
cd stock-monitor
npm ci
```

## 本地启动

后端固定监听 `http://127.0.0.1:8001`：

```bash
cd backend
./venv/bin/python main.py
```

前端固定监听 `http://localhost:4000`：

```bash
cd stock-monitor
npm run dev
```

前端通过 `/api/backend/...` 服务端代理访问后端，浏览器端不保存后端私密 Token。

## 验证命令

后端：

```bash
cd backend
./venv/bin/python -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/tmp/codex-pycache ./venv/bin/python -m py_compile market_calendar.py database.py main.py
./venv/bin/python -m pip check
```

前端：

```bash
cd stock-monitor
npx tsc --noEmit
npm run lint
npm run build
```

## 数据真实性

- 行情、新闻、公告和财务信息必须来自可追溯公开来源。
- 计算数据需要明确口径。
- AI 内容必须标记为模型或规则判断。
- 数据缺失或抓取失败时返回 `null`、空数组或明确失败状态，禁止用 Mock 或随机数填充生产页面。
