# 项目协作规范

## 目标与边界

本项目是单组织私有部署的领域 Agent 平台。管理员配置模型、提示词、工具、知识库及权限；成员只使用获授权的已发布 Agent。当前实施需求以 `docs/requirements.md`、路线图以 `docs/roadmap.md` 为准。公开代码不得包含组织资料、真实提示词、连接凭证或运行数据。

## 目录职责

- `backend/app/`：FastAPI、数据模型、服务、Agent 运行时与异步任务。
- `backend/tests/`：权限、运行、工具副作用、文档与集成测试。
- `frontend/`：React/TypeScript 成员门户与管理后台。
- `deploy/`、`scripts/`：容器入口、部署、冒烟测试、备份恢复。
- `docs/`：需求、架构、接口、运维与验证记录。
- `.github/workflows/`：自动检查和仅手动触发的 GHCR 发布。

## 必须遵守

1. 成员响应使用独立的白名单类型，不返回 ORM 实体或内部配置。
2. 密钥加密存储，连接器调用时注入，禁止进入模型上下文、浏览器、日志或镜像。
3. 所有对象访问检查当前身份和授权；会话附件、引用、事件及确认均校验所有者。
4. 写工具按管理员策略执行，默认确认；确认绑定参数摘要，参数变化重新确认。
5. 外部写入可能已提交时不得盲目重试。用调用账本、幂等键和结果待核实状态处理恢复。
6. 每次运行固定配置快照；权限撤销和资源停用在执行前再次检查。
7. 首版不执行任意代码，不启用沙盒；OpenSandbox 作为下一阶段适配器。
8. 数据结构变更必须提供 Alembic 迁移；运行数据和检查点删除遵守权限及保留规则。
9. 保持模块职责清晰，避免集中在单一大文件；提交前运行相关测试。
10. README、接口契约与实际实现同步，禁止将未实现或未验证能力标记完成。

## 开发与验证

Python 使用 uv 锁定依赖，前端使用 npm 锁文件。正式命令在工程建立后随 README 同步：`uv sync --frozen --group dev`、`uv run pytest`、`uv run ruff check backend`；前端在 `frontend` 执行 `npm ci`、`npm run check`、`npm test`、`npm run build`。只运行与变更有关的测试，最终交付运行完整验证。

## Git 与发布

用户已授权在本目录建立 `main` 并推送公开仓库 `compcj/vibe-domain-agent`。禁止强推或覆盖无关历史。镜像为 `ghcr.io/compcj/vibe-domain-agent`；只有手动工作流可以发布。Actions 使用最小权限 GITHUB_TOKEN、固定 action 提交 SHA。发布前测试、构建并冒烟验证，保留提交 SHA 标签及 digest，失败不更新 latest。
