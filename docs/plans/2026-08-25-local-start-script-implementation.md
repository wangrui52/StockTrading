# 本地一键启动脚本实现计划

> **致 Claude：** 必须使用子技能 dev-executing-plans 逐任务执行此计划。

**目标：** 创建可双击或从终端执行的一键本地启动脚本。

**架构：** 根目录 `start_local.command` 统一编排 Docker Desktop、Compose、演示数据和健康检查。脚本只创建缺失配置并调用已有幂等能力，不维护第二套启动逻辑，也不删除数据卷。

**技术栈：** zsh、Docker Desktop、Docker Compose、curl、macOS open。

---

### 任务 1：启动脚本契约

**文件：**
- 创建：`start_local.command`
- 创建：`tests/test_start_local_script.sh`

**步骤 1：编写失败的静态契约测试**

测试检查脚本存在且可执行，并包含项目目录定位、Docker daemon 检查、Compose 启动、幂等演示数据、健康检查和浏览器打开命令。

**步骤 2：运行测试确认其失败**

运行：`zsh tests/test_start_local_script.sh`

预期：FAIL，提示 `start_local.command` 不存在。

**步骤 3：编写最小实现**

脚本使用 `set -euo pipefail`，以 `${0:A:h}` 定位项目根目录；Docker 未运行时启动 Docker Desktop并限时等待；缺失 `.env` 时从示例复制；调用 Compose、演示数据脚本和健康接口，成功后打开浏览器。

**步骤 4：运行静态验证**

运行：

```bash
zsh -n start_local.command
zsh tests/test_start_local_script.sh
```

预期：两项均通过。

### 任务 2：文档和真实运行验证

**文件：**
- 修改：`README.md`

**步骤 1：补充一键启动入口**

在本地启动章节优先说明双击和终端两种执行方式，并保留手动源码启动作为开发方式。

**步骤 2：执行真实启动**

运行：`./start_local.command`

预期：四个容器启动，健康接口成功，演示批次存在。

**步骤 3：验证幂等**

再次运行：`START_LOCAL_NO_OPEN=1 ./start_local.command`

预期：已有批次被复用，不重复创建数据，服务保持健康。

**步骤 4：清理运行态并回归**

运行：

```bash
docker compose -f deploy/docker-compose.yml down
git diff --check
```

预期：容器和网络停止，命名数据卷保留，代码检查通过。

### 任务 3：提交

**步骤 1：提交实现**

```bash
git add start_local.command tests/test_start_local_script.sh README.md docs/plans/2026-08-25-local-start-script-implementation.md
git commit -m "feat: 增加本地一键启动脚本"
```

**步骤 2：确认工作区**

运行：`git status --short`

预期：无输出。
