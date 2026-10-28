# 验证记录

此文档记录实际运行结果及对应提交。GitHub/GHCR、部署文件和模拟环境验证已交付；生产就绪仍须完成目标服务器和业务验收。镜像当前 digest 以对应发布工作流的 Summary 为准。

## 已运行

- 基础后端：23 项认证、权限、管理、限流与安全回归测试通过；SQLite Alembic 升级/检查/降级通过。
- 前端：20 项 API、流事件、确认组件、账号切换隔离、确认重放、引用重新鉴权、部分上传失败及确认状态时序测试通过；TypeScript 检查及 Vite 生产构建通过。
- Playwright Chromium：成员操作、响应式布局和管理员配置/授权/发布/模型能力共3个场景通过，使用可控 API fixture；不等同于完整后端联调。
- 运维脚本：18 项测试通过，覆盖配置密钥生成、文件归档边界、镜像与密钥恢复、SHA 复用、main 前进、冒烟失败与注册表异常处理。
- GitHub：公开仓库 `main` 已推送工程实现。
- 首轮 [Linux CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40637361712) 在 `06735cc` 通过：60 项 Python 测试（含真实 PostgreSQL/Redis）、11 项当时前端单元测试、2 项 Chromium 测试、应用镜像构建、迁移、非 root、API/前端、两个 Worker 和真实队列工具确认流程。此记录不代表后续提交自动通过。
- 基础版本 `6ecfd6116be597948c6835e7b0af6f5e59f6e2fa` 的 [CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40643894843) 与[手动发布](https://github.com/compcj/vibe-domain-agent/actions/runs/40643895359)通过：102 项 Python 测试、容器启动、Compose 部署以及真实 PostgreSQL/文件联合备份恢复。恢复后验证了账号、会话、文件、待确认任务、LangGraph 检查点恢复和仅一次外部写入。
- 基础镜像为 `ghcr.io/compcj/vibe-domain-agent@sha256:8e4c2eaed01d027468c9113422e323629157d04879f452256785097ab392907d`，对应完整 SHA 标签和当次 latest。已在空 Docker 凭据目录中[匿名拉取并启动验证](https://github.com/compcj/vibe-domain-agent/actions/runs/40644781425)，无需 GitHub/GHCR 登录。
- 知识库及审查修复在 `0bd08c2` 的 [完整 CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40651984945) 通过：176 项 Python 测试（包含所有 PostgreSQL/Redis 用例）、15 项前端单元测试和3项浏览器场景。容器内实际完成异步解析、嵌入、pgvector 检索、私有附件下载、引用鉴权、同名替换与文档删除；联合恢复后验证了真实附件、引用、会话、待确认检查点及一次外部写入。
- 更新后的匿名验证按镜像 OCI 源码提交检出对应校验脚本；[复验基础镜像通过](https://github.com/compcj/vibe-domain-agent/actions/runs/40651985760)，主分支增加功能不会让旧镜像使用不匹配的测试脚本。
- 交付修复 `639cdbc` 的 [完整 CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40655124290) 通过：186 项 Python 测试、19 项前端单元测试、3 项浏览器测试及全部容器联调。真实恢复场景包含无注册表 digest 的本地镜像归档、源容器密钥与 `.env` 不同、密钥中含字面 `$`、目标 shell 提供错误镜像与密钥；恢复后加密的模型/工具凭证、附件、引用及待确认运行均可继续使用。
- [模拟容量验收](https://github.com/compcj/vibe-domain-agent/actions/runs/40652736382)通过：`0bd08c2` 后端，10 个成员并发持续 1,800.67 秒，完成 14,578 次任务、失败 0 次、错误率 0%。该运行还完成知识/附件、成员隔离、确认及重复提交检查，受控外部写入只提交 1 次。后续 `639cdbc` 仅改动界面和交付脚本，应用后端未变；此结果不代表真实模型速度、目标服务器容量或业务合格率。
- 整体审查及修复复核通过，本轮发现已全部关闭。`37f8361` 的 [CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40659475886)通过：193 项 Python 测试、20 项前端测试和3项浏览器场景。新增回归覆盖已建立事件流的会话撤销/过期、确认卡片时序、迁移分批回填。真实 Docker 检查证明空排除规则会暴露合成私密文件，当前规则则保留公共对照文件并排除私密文件；应用容器及联合备份恢复再次通过。
- `391e19b` 的 [完整 CI](https://github.com/compcj/vibe-domain-agent/actions/runs/40660239919)通过：195 项 Python 测试、20 项前端测试、3 项浏览器场景及全部容器验证。新增 PostgreSQL 测试从上一迁移版本升级，实际使用服务端游标分批回填 1,201 条合格历史引用，并验证不应继承的来源被排除；此提交只增加测试。
- 完整版本 `391e19b4ab1435a87cc8df5e04608ba86e4d6fc9` 已[手动发布](https://github.com/compcj/vibe-domain-agent/actions/runs/40660585156)。镜像为 `ghcr.io/compcj/vibe-domain-agent@sha256:a8d9b6e5b668de2b0c6c0076b98186a8525013583edfa579f78a78419298c047`，完整 SHA 标签与 OCI 源码信息一致；按 digest 重新拉取并冒烟验证后推广为当次 latest。
- 完整版本在空 Docker 凭据目录中[匿名拉取并启动通过](https://github.com/compcj/vibe-domain-agent/actions/runs/40661888265)，包括真实索引、检索、引用、工具确认和 Worker 检查。
- 基础镜像 `6ecfd61` → 完整镜像 `391e19b` 的[升级与备份回滚通过](https://github.com/compcj/vibe-domain-agent/actions/runs/40661893367)。升级后恢复旧版待确认检查点并只提交一次外部写入；实际确认旧镜像拒绝新版 schema；随后把升级前数据库与文件备份恢复到另一个空项目，再次验证待确认恢复与一次写入。两个路径使用独立模拟业务系统，此测试不代表能够撤销真实外部系统已经发生的操作。
- 新增会话实时校验后的[容量复验通过](https://github.com/compcj/vibe-domain-agent/actions/runs/40659784449)：`37f8361` 应用逻辑，10 个成员持续 1,800.91 秒，完成 14,571 次任务、失败 0 次、错误率 0%，知识/附件、隔离及重复提交检查通过。后续 `391e19b` 仅增加迁移测试，最终交付再同步文档，未改变本轮受测应用逻辑。

## 现场与业务验收（待环境准备）

- 两个试点 Agent 各至少 30 个业务案例，由业务负责人确认合格率达到 85%，权限和写控制全部通过。
- 目标服务器部署、业务试点、升级及数据库兼容回滚：用户确认暂无目标服务器，本次交付 GitHub/GHCR、部署文件与模拟环境验证，现场验收留到服务器准备后。
