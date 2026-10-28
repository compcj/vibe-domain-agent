# API v1 接口契约

所有路由以 `/api/v1` 开头，JSON 字段使用 snake_case，ID 为 UUID 字符串。错误形如 `{detail: string}`，验证错误的 detail 可为数组；列表接口默认返回数组。成员 DTO 不暴露内部配置。会话 Cookie 为 HttpOnly，写请求发送认证响应中的 `X-CSRF-Token`，不开放注册。管理操作和本人草稿调试可使用管理员权限；成员资料始终检查所有者，调试写工具仍检查工具授权。

## 账号与管理配置

- POST /auth/login `{email,password}` → `{user:{id,email,display_name,role,group_ids,active}, csrf_token}`，同时设置 Cookie。
- GET /auth/me → same shape; POST /auth/logout → 204; POST /auth/password `{current_password,new_password}`.
- GET /admin/users → User[]；POST /admin/users `{email,display_name,password,role:'admin'|'member',group_ids:[],active:true}`；PATCH /admin/users/{id} 修改上述字段（密码除外）；POST .../reset-password `{password}`；POST /admin/users/import `{users:[创建成员的请求...]}`。
- GET/POST /admin/groups; PATCH/DELETE /admin/groups/{id}; group `{id,name,description}`.
- GET/POST /admin/models; PATCH/DELETE /admin/models/{id}; config `{name,kind:'chat'|'embedding',base_url,model,api_key,enabled:true}`; 读取隐藏 api_key，提供 has_api_key。POST .../{id}/test 返回 `{ok,message,capabilities}`。
- GET/POST /admin/agents; GET/PATCH /admin/agents/{id}; `{id,name,description,examples:[],enabled:true,allowed_group_ids:[],draft:{model_id,system_prompt,temperature:0.7,max_tokens:4096,tool_ids:[],knowledge_base_ids:[],max_steps:20,timeout_seconds:300},published_version_id}`.
- POST /admin/agents/{id}/publish → version; GET .../versions → versions; POST .../rollback `{version_id}`.
- GET /agents → `{id,name,description,examples}[]`，只包含已授权的已发布 Agent。
- GET /admin/metrics 与 GET /admin/audit → 汇总统计与脱敏审计。

## 会话与运行

- GET/POST /conversations；POST `{agent_id,title?}` → `{id,agent_id,title,created_at,active_run_id}`；GET/DELETE /conversations/{id}。列表中的 active_run_id 用于刷新页面后恢复订阅。
- GET /conversations/{id}/messages → `{id,role,content,citations:[],created_at,run_id}[]`.
- POST /conversations/{id}/runs `{message,attachment_ids:[]}` → `{id,status}`（202）；禁止额外字段。
- GET /runs/{id} → `{id,status,error?,approvals:[],created_at}`; POST /runs/{id}/cancel.
- GET /runs/{id}/events?after=0 → SSE，id 为序号，event 为事件类型，data 为 JSON。类型包括 status `{status,message}`、delta `{text}`、citation `{id,title,locator,excerpt}`、approval `{id,title,summary,parameters,expires_at}`、result `{message}`、done `{status}`、error `{message}`。不返回框架原始事件，通过 Last-Event-ID 或 after 恢复订阅。
- 事件流每批重新校验原登录会话；登出、密码修改/重置或会话过期后关闭，不继续推送新内容。关闭事件连接不等于取消后台任务。等待确认时通过 `/runs/{id}` 读取当前有效确认记录，不能把历史 approval 事件直接当作待执行操作。
- 模型 HTTP 失败在 `error`/`error.message` 中返回固定的错误分类、处理建议和 HTTP 状态，包括请求拒绝、上下文超限、鉴权/权限、资源不存在、请求过大、限流/配额和上游故障；不转发上游原文、端点、模型标识或凭证。上下文错误识别只读取最多 16 KiB 的非压缩错误正文，其他情况使用对应状态的通用提示；HTTP 拒绝不会自动重试模型请求。
- POST /approvals/{id}/decision `{approved:boolean}`；仅发起成员可以决定，决定持久化，重复请求不会重复执行工具。
- POST /messages/{id}/feedback `{rating:1|-1,comment:''}`.
- POST /admin/agents/{id}/debug `{message}` → 同一运行响应类型；使用管理员本人私有会话与草稿。

## 工具

- GET/POST /admin/tools；PATCH/DELETE /admin/tools/{id}；字段 `{id,name,title,description,transport:'http'|'mcp',endpoint,method:'POST',mcp_tool_name:'',input_schema:{},fixed_arguments:{},headers:{},effect:'read'|'write',policy:'auto'|'confirm',allowed_group_ids:[],timeout_seconds:30,idempotency_header:'',enabled:true}`。Headers 加密保存，GET 只返回 has_headers。
- POST /admin/tools/discover `{endpoint,headers:{}}` → 已发现工具列表，不自动注册。
- GET /admin/tool-calls?status=outcome_unknown → 供结果核实使用的脱敏调用摘要。
- POST /admin/tool-calls/{id}/resolve `{outcome:'succeeded'|'failed',note}` → 核实审计，不重新执行工具。

## 知识库与文件

- GET/POST /admin/knowledge-bases `{name,description,embedding_model_id}`; PATCH/DELETE /admin/knowledge-bases/{id}.
- GET/POST /admin/knowledge-bases/{id}/documents（POST 使用 multipart file）；DELETE /admin/documents/{id}；POST .../retry；POST /admin/knowledge-bases/{id}/search `{query}`。
- POST /files 使用 multipart conversation_id,file → `{id,name,status,error?}`；GET /files?conversation_id=...；GET /files/{id}；GET /files/{id}/download；DELETE /files/{id}。
- GET /citations/{id} → 经过鉴权的 `{id,title,locator,excerpt}`。

同名知识库上传保留文档 ID、增加 revision，旧来源立即退出检索；只允许重试 failed 文档。所有文件响应白名单为 `{id,name,status,error}`，error 可为 null，状态为 queued/processing/indexing/ready/failed。知识库仍有任意文档时切换 embedding_model_id 返回409；Agent 草稿或保留版本引用的知识库不能删除。检索测试返回片段数组，其中预览 id 是 chunk ID，不能用于成员引用接口。

成员引用必须属于本人；知识库来源依据原运行的固定绑定及当前 Agent 使用授权校验。单纯发布或回滚不改变已有运行的绑定；来源删除、替换或撤权返回404。关联助手历史回答、后续继承该来源的回答及事件会投影为不可用提示，不进入后续模型上下文。普通无来源依赖的回答不受影响。管理员只能查看本人草稿调试产生的引用，不能访问其他成员的资料。

一个文件可能对应多个片段引用。成员界面将其放在默认折叠的参考资料列表中，打开片段时重新请求 `/citations/{id}`；收起列表会清除已展示片段，再次展开后重新鉴权。发送给模型的参考资料保留不可信数据标记，与管理员提示组成唯一的首条系统消息；已有检查点的连续首部系统消息在调用边界合并，历史对话与工具消息顺序不变。

模型管理 DTO 增加 `capabilities` 和 `tested_at`。对话模型能力键为 streaming/tool_calling；嵌入模型为 embedding/embedding_dimensions。未测试的键缺省；可选工具调用未通过不会否定已验证的纯对话能力。修改端点、模型标识、用途或凭证会清除测试结果。
