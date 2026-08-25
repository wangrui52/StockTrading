# ADR-001 技术栈与源码隔离

## 状态
accepted

## 决策
采用 FastAPI + React/TypeScript + SQLite；SQLAlchemy 预留 PostgreSQL；Docker Compose + Nginx 作为远程部署基础。

## 理由
Python 与 AkShare/Pandas 匹配，React 适合交互式数据工具，REST/OpenAPI 使前后端隔离并可独立部署。

## 备选
Django + React 过重；Node.js + React 会引入额外 Python 数据进程。

## 后果
需要维护 Python 与 TypeScript 两套工具链，但业务计算集中在后端，前端不复制规则。

