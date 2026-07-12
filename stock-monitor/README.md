# 前端：stock-monitor

本目录是“量化监测-股票”的 Next.js 前端。完整项目说明见根目录 [`README.md`](../README.md)。

## 本地开发

```bash
npm ci
npm run dev
```

固定访问地址：`http://localhost:4000`。

后端固定地址为 `http://127.0.0.1:8001`，前端通过 `/api/backend/...` 服务端代理访问后端。

## 检查

```bash
npx tsc --noEmit
npm run lint
npm run build
```
