# 验证记录

此文档记录实际运行结果。首次完整交付前保持“实施中”，没有目标服务器和业务验收证据时不标记生产就绪。

## 已运行

- 基础后端：23 项认证、权限、管理、限流与安全回归测试通过；SQLite Alembic 升级/检查/降级通过。
- 前端：15 项 API、流事件、确认组件、账号切换隔离、确认重放和引用重新鉴权测试通过；TypeScript 检查及 Vite 生产构建通过。
- Playwright Chromium：成员操作、响应式布局和管理员配置/授权/发布/模型能力共3个场景通过，使用可控 API fixture；不等同于完整后端联调。
- 运维脚本：3 项配置密钥生成、备份文件往返和恶意归档边界测试通过；5种发布保护场景验证 SHA 复用、main 前进、冒烟失败与注册表异常处理。
- GitHub：公开仓库 `main` 已推送工程实现。
- 首轮 [Linux CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40637361712) 在 `06735cc` 通过：60 项 Python 测试（含真实 PostgreSQL/Redis）、11 项当时前端单元测试、2 项 Chromium 测试、应用镜像构建、迁移、非 root、API/前端、两个 Worker 和真实队列工具确认流程。此记录不代表后续提交自动通过。
- 基础版本 `6ecfd6116be597948c6835e7b0af6f5e59f6e2fa` 的 [CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40643894843) 与[手动发布](https://github.com/compcj/vibe-domain-agent/actions/runs/40643895359)通过：102 项 Python 测试、容器启动、Compose 部署以及真实 PostgreSQL/文件联合备份恢复。恢复后验证了账号、会话、文件、待确认任务、LangGraph 检查点恢复和仅一次外部写入。
- 基础镜像为 `ghcr.io/compcj/vibe-domain-agent@sha256:8e4c2eaed01d027468c9113422e323629157d04879f452256785097ab392907d`，对应完整 SHA 标签和当次 latest。已在空 Docker 凭据目录中[匿名拉取并启动验证](https://github.com/compcj/vibe-domain-agent/actions/runs/40644781425)，无需 GitHub/GHCR 登录。
- 知识库实现首轮本地后端129通过、7项服务测试跳过，SQLite迁移通过；独立审查发现的继承引用、文件清理及并发保护问题正在修复。真实 pgvector 存储/不同维度检索用例已在 CI 通过；该轮另外5个 PostgreSQL fixture 缺少扩展初始化，尚未通过整套验收。

## 待完成

- 知识库/文件审查修复、完整后端及容器联调，实际私有附件/引用联合备份恢复。
- 包含知识库功能的后续镜像发布、匿名拉取及跨版本升级/数据库兼容性/备份回滚验证。
- 10 并发持续 30 分钟模拟负载，错误率低于 1%、无串用或重复写入。
- 两个试点 Agent 各至少 30 个业务案例，由业务负责人确认合格率达到 85%，权限和写控制全部通过。
- 目标服务器部署、业务试点、升级及数据库兼容回滚：用户确认暂无目标服务器，本次交付 GitHub/GHCR、部署文件与模拟环境验证，现场验收留到服务器准备后。
