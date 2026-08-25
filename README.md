# A 股交易辅助决策工具

按照 `A股交易辅助决策工具_PRD_V1.1.md` 实现的前后端隔离项目。

## 目录

- `backend/`：FastAPI、领域计算、数据同步与 SQLite。
- `frontend/`：React/TypeScript 用户界面。
- `deploy/`：本地和远程容器部署配置。
- `docs/`：架构、计划、部署和运维文档。

## 当前开发命令

```bash
make install
make test
make dev-backend
make dev-frontend
```

后端默认监听 `127.0.0.1:8000`，前端默认监听 `127.0.0.1:5173`。

本工具仅用于个人研究和信息整理，不构成投资建议。
