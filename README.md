# Vibe Domain Agent · 领域 Agent 工作台

面向单组织私有部署的 Agent 平台。管理员配置模型、提示词、工具、知识库与授权；成员在浏览器中选择获授权的助手，开展问答、文档分析及可确认的业务操作。

代码仓库：[compcj/vibe-domain-agent](https://github.com/compcj/vibe-domain-agent)。应用镜像：`ghcr.io/compcj/vibe-domain-agent`。本仓库正在实施首版；详细实现和验证状态以[路线图](docs/roadmap.md)与[验证记录](docs/verification.md)为准，不将生成的工程文件视为生产验收完成。

## 功能与边界

已实现：独立账号、用户组及授权、模型与能力测试、Agent 草稿/调试/发布/回滚、多轮流式对话、HTTP/MCP 工具、业务确认、调用账本、审计和管理界面。基础镜像已完成手动 GHCR 发布、匿名拉取、容器启动和联合备份恢复。知识库、附件与引用已实现，正在进行独立审查和完整容器联调，详细证据见验证记录。

文档解析支持文本型 PDF、DOCX、TXT、Markdown、CSV、XLSX；不支持扫描件 OCR、加密文件、宏执行、代码计算。文本和 CSV 使用 UTF-8，可含 BOM。工具通过管理员选择的 HTTP 或 MCP Streamable HTTP 服务连接，不运行 stdio 程序。组织服务账号决定业务数据范围，不继承成员在外部业务系统中的个人权限。

OpenSandbox、企业 SSO、多租户、成员自行创建 Agent、可视化编排和跨会话长期记忆未提供。OpenSandbox 计划在首版之后接入，当前配置不能启用沙盒。

## 架构与环境

React/TypeScript → Nginx → FastAPI；LangGraph + Celery 执行对话，PostgreSQL/pgvector 保存数据与检查点，Redis 排队，持久文件卷保存资料。API、执行 Worker 和索引 Worker 使用同一应用镜像。详见[架构](docs/architecture.md)、[需求](docs/requirements.md)、[接口契约](docs/api-contract.md)和[协作规范](AGENTS.md)。

- 开发：Python 3.12、uv 0.13.6、Node.js 24、npm，以及 PostgreSQL/pgvector 和 Redis（本地轻量测试可用 SQLite）。
- 部署：Linux/amd64、Docker Engine、Docker Compose v2。建议起步 4 CPU / 8 GB RAM，再通过容量测试调整。部署用户无需 Node.js 或 Python 工具链。
- 本地未安装 Docker 时，单元及界面测试可运行；容器、真实 PostgreSQL/Redis 验证由 Linux CI 完成。

## 从源码开发

```sh
git clone https://github.com/compcj/vibe-domain-agent.git
cd vibe-domain-agent
uv sync --frozen --group dev
uv run python scripts/configure.py --development
uv run python -m app.cli migrate
uv run python -m app.cli init-admin
uv run uvicorn app.main:app --reload
```

首次初始化交互输入管理员邮箱、名称与至少 12 字符的密码，没有内置账号或默认密码。Windows 使用 PowerShell 执行上述命令。`.env` 中本地 SQLite 默认位于 `data/app.db`；异步任务需要 Redis，默认连接 `redis://127.0.0.1:6379/0`。使用本地 PostgreSQL 时修改 `DATABASE_URL` 后再迁移。

另外开启前端、执行 Worker、索引 Worker 和定时调度：

```sh
cd frontend
npm ci
npm run dev
```

在仓库根目录的其他终端运行：

```sh
uv run celery -A app.worker.celery_app worker -Q runs --concurrency=10 --loglevel=WARNING
uv run celery -A app.worker.celery_app worker -Q indexing --concurrency=2 --loglevel=WARNING
uv run celery -A app.worker.celery_app beat --loglevel=WARNING
```

Celery 生产环境使用 Linux；Windows 开发可加 `--pool=solo` 单进程调试，完整并发与恢复测试在 Linux 运行。浏览器访问 `http://127.0.0.1:5173`。前端把 `/api` 与 `/health` 代理给 `127.0.0.1:8000`。

管理员依次创建用户组和成员 → 模型连接 → 知识库/工具 → Agent 草稿 → 授权与调试 → 发布。Agent 和工具都需要单独授权。没有授权组时，管理员在成员门户也看不到对应 Agent。

知识库文档同名上传会替换原版本并重新索引；旧引用随即失效。更换嵌入模型前先删除库内文档，更换后重新上传；修改服务端嵌入空间也需要重建索引。检索采用 pgvector 精确余弦排序，初始阈值和切分策略需结合业务资料评测。删除、替换资料或撤销授权后，依赖失效来源的历史助手回答会显示不可用，避免从历史消息和事件重放读取旧片段；成员原始提问保留。

验证命令：

```sh
uv run pytest
uv run ruff check backend scripts
cd frontend
npm run check
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

## 从 GHCR 镜像部署

下载本仓库部署文件（或克隆仓库，不需要编译），进入目录后生成环境配置：

```sh
docker pull ghcr.io/compcj/vibe-domain-agent:latest
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD:/output" ghcr.io/compcj/vibe-domain-agent:latest \
  python /app/scripts/configure.py --output /output/.env
```

生产建议把 `.env` 中 `APP_IMAGE` 改为发布 Summary 的 `ghcr.io/compcj/vibe-domain-agent@sha256:...`。一个变量同时指定 API、迁移、所有 Worker 与调度器的版本。

使用有效证书，在 `tls/fullchain.pem` 与 `tls/privkey.pem` 放置证书和私钥，然后：

```sh
export COMPOSE_FILE=compose.yaml:compose.https.yaml
docker compose up -d --wait
docker compose run --rm api init-admin
```

通过服务器域名的 HTTPS 地址访问。在部署、升级、备份与恢复的终端中保持同一 `COMPOSE_FILE`，使脚本也加载 HTTPS 配置。也可以由现有网关终止 HTTPS，再转发到默认仅绑定环回地址的 `127.0.0.1:8080`，此时不设置 `COMPOSE_FILE`，使用 `docker compose up -d --wait`。不要把明文 HTTP 暴露公网。生产登录 Cookie 使用 Secure，直接 HTTP 登录不会维持会话。

仅在本地体验时把 `APP_ENV=development`，启动基本 Compose 后访问 `http://127.0.0.1:8080`。数据库和 Redis 不向宿主机发布端口。应用 UID 为 10001，使用命名卷。改用绑定目录时需授予该 UID 文件写入权限。

从源码构建同样的部署镜像：设置 `APP_IMAGE=vibe-domain-agent:local`，运行 `docker compose -f compose.yaml -f compose.build.yaml build api`，再按上述命令启动。

## 环境变量

| 变量 | 用途 |
|---|---|
| `APP_ENV` | `production` 强制安全密钥与生产约束；本地使用 `development` |
| `APP_IMAGE` | Compose 各应用服务共同使用的镜像，生产固定 digest |
| `SECRET_KEY` | 随机签名密钥，由配置脚本生成 |
| `ENCRYPTION_KEY` | Fernet 密钥，加密模型与工具凭证；必须备份 |
| `POSTGRES_PASSWORD` | Compose 数据库口令，脚本生成安全的十六进制值 |
| `DATABASE_URL` | 源码运行的 SQLAlchemy URL；Compose 自动设置内部地址 |
| `REDIS_URL` | 源码运行的 Redis URL；Compose 自动设置内部地址 |
| `STORAGE_DIR` / `FRONTEND_DIST` | 文件存储和构建后前端目录 |
| `HTTP_PORT` / `HTTPS_PORT` | 默认环回 8080 / 对外 443 |

配置脚本不覆盖现有文件、不输出密钥。`.env`、TLS 私钥、上传文件、数据库、日志和备份同时排除在 Git 与 Docker 构建上下文之外。不要把组织资料、真实业务提示词或连接凭证放进示例代码。

## 手动发布镜像

在 [GitHub Actions](https://github.com/compcj/vibe-domain-agent/actions/workflows/publish-ghcr.yml) 选择 **Publish GHCR (manual)** → **Run workflow** → `main`，或：

```sh
gh workflow run publish-ghcr.yml --repo compcj/vibe-domain-agent --ref main
gh run list --repo compcj/vibe-domain-agent --workflow publish-ghcr.yml
```

工作流锁定运行开始时的 `main` SHA，使用锁文件测试、构建与数据库/Redis/Worker 冒烟验证，再发布不可变 `sha-<完整SHA>`。同一 SHA 已存在时校验并复用。拉取发布后的 digest 再验证，只有 `main` 仍指向该提交才更新 `latest`。仓库级并发组串行发布；普通 push、PR、Release 不发布镜像。

首次发布后，在 Package settings 显式设置 Public，并验证匿名拉取。公开仓库不代表 Package 自动公开。后续部署拉取方式：

```sh
docker pull ghcr.io/compcj/vibe-domain-agent:sha-<完整提交SHA>
docker pull ghcr.io/compcj/vibe-domain-agent@sha256:<发布摘要>
```

可手动运行 `gh workflow run verify-public.yml --repo compcj/vibe-domain-agent --ref main`，在空 Docker 凭据目录中匿名拉取，并检查迁移、API、前端和 Worker。该验证工作流不发布镜像。30 分钟模拟容量验证使用 `validate-load.yml`，结果写入 Actions Summary。

两个已发布版本之间的升级和备份回滚验证使用 `verify-upgrade.yml`，输入前后镜像标签或 digest。工作流先固定两个 digest，在临时项目中迁移并恢复旧检查点，检查旧镜像能否识别新 schema，再把升级前备份恢复到新的空项目。模拟业务服务彼此隔离；真实外部系统在备份之后已发生的操作仍需人工核实。

参考：[GitHub 手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[GHCR 权限与认证](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

## 升级、回滚与备份

升级前安排维护窗口，执行 `bash scripts/backup.sh`。将 `APP_IMAGE` 换为目标 digest，运行 `docker compose pull`，然后 `docker compose up -d --wait`（HTTPS 部署保持上述 `COMPOSE_FILE`）。迁移服务先完成应用表与 LangGraph 检查点表的初始化，应用再启动；业务运行不会创建 PostgreSQL 表或索引。

回滚先检查目标版本是否兼容当前数据库 schema；更换旧镜像不等于数据库回滚。兼容时恢复旧 digest 并重新启动；不兼容时在新部署中恢复升级前的联合备份，再切换入口。避免对仍有业务写入的库直接运行降级迁移。

备份脚本检查正在运行的 API、Workers 和调度器使用同一镜像及密钥配置，再暂停这些服务与入口，联合保存 PostgreSQL（含检查点）、文件卷和实际生效的密钥，随后恢复服务。镜像以不可变 digest 与镜像 ID 记录在版本化 `recovery.json` 中；没有仓库 digest 的源码构建镜像会用 `docker save` 一并保存为 `application-image.tar`，恢复时加载并校验 ID。当前终端的 `APP_IMAGE` 或密钥覆盖值不会替代正在运行的值。辅助脚本从当前 `scripts/` 挂载到应用镜像执行，宿主机仅需 Docker Compose、Bash 和常用命令行工具。

备份文件权限为 600，默认保留 7 天；备份包含私密资料和解密密钥，离机存放前应加密。源码镜像归档可能较大，请预留磁盘空间。示例 systemd service/timer 位于 `deploy/`，修改部署路径后由运维启用每日任务。仅配置文件不会自动启用系统定时器。

恢复到**全新的目录、Compose 项目和空卷**：安全解包可信备份，把 `config.env` 复制为 `.env`，设置独立 `COMPOSE_PROJECT_NAME` 和目标端口，保持所需的 HTTPS `COMPOSE_FILE`，运行 `bash scripts/restore.sh <解包目录> --confirm-empty-target`。脚本先核验版本化清单、镜像 ID 与配置一致性，再启动目标数据库；缺少或不一致的元数据会拒绝恢复，不会自动采用 `latest`。没有 `recovery.json` 的旧备份需要人工核实原镜像与生效密钥，不能直接使用此自动恢复流程。

恢复保留目标端口、项目名及 HTTPS 配置，强制使用备份中的镜像和有效密钥，不受当前 shell 的 `APP_IMAGE`、`SECRET_KEY`、`ENCRYPTION_KEY` 等覆盖值影响，并把恢复值保存到目标 `.env`。后续普通启动前清除旧的 shell 覆盖值，确保与恢复后的 `.env` 一致。脚本拒绝非空数据库和文件卷；完成后核对会话、引用、待确认任务及外部业务结果。

## 常见问题

- 没有可选助手：检查 Agent 是否发布/启用，成员所属组是否有使用权限；工具授权另外配置。
- 任务一直排队：确认 Redis、`runs` Worker 和 beat 在运行，查看 `docker compose ps`；文档解析需要 `indexing` Worker。
- 登录后又返回登录页：生产环境必须使用 HTTPS，并检查浏览器 Cookie 与反向代理地址。
- 外部操作“结果待核实”：先到业务系统确认结果，管理员在运行与审计中登记结论，不要重复提交同一操作。
- 文档解析失败：查看文件状态提示；确认格式、大小、是否加密或扫描件，不执行宏和公式计算。
- 生产试点：真实模型效果、业务数据授权、30 分钟容量测试和用户验收需要目标环境验证，详见验证记录。
