# API contract v1

All routes use /api/v1. JSON snake_case. UUID string ids. Errors `{detail: string}` (validation errors may be arrays). List endpoints return arrays unless stated. Member DTOs never expose internal config. Session cookies HttpOnly; unsafe authenticated requests send X-CSRF-Token obtained from auth response. No self-registration. Admin role bypasses resource grants only for admin operations/debug; member data always owner checked.

## Foundation / administration

- POST /auth/login `{email,password}` → `{user:{id,email,display_name,role,group_ids,active}, csrf_token}` and cookie.
- GET /auth/me → same shape; POST /auth/logout → 204; POST /auth/password `{current_password,new_password}`.
- GET /admin/users → User[]; POST /admin/users `{email,display_name,password,role:'admin'|'member',group_ids:[],active:true}`; PATCH /admin/users/{id} same mutable fields excluding password; POST .../reset-password `{password}`; POST /admin/users/import `{users:[create payload...]}`.
- GET/POST /admin/groups; PATCH/DELETE /admin/groups/{id}; group `{id,name,description}`.
- GET/POST /admin/models; PATCH/DELETE /admin/models/{id}; config `{name,kind:'chat'|'embedding',base_url,model,api_key,enabled:true}`; read hides api_key, adds has_api_key. POST .../{id}/test returns `{ok,message}`.
- GET/POST /admin/agents; GET/PATCH /admin/agents/{id}; `{id,name,description,examples:[],enabled:true,allowed_group_ids:[],draft:{model_id,system_prompt,temperature:0.7,max_tokens:4096,tool_ids:[],knowledge_base_ids:[],max_steps:20,timeout_seconds:300},published_version_id}`.
- POST /admin/agents/{id}/publish → version; GET .../versions → versions; POST .../rollback `{version_id}`.
- GET /agents → `{id,name,description,examples}[]` authorized published only.
- GET /admin/metrics and GET /admin/audit → aggregate statistics and redacted audit events.

## Conversations / runs (next task)

- GET/POST /conversations; POST `{agent_id,title?}` → `{id,agent_id,title,created_at,active_run_id}`; GET/DELETE /conversations/{id}. Listing includes active_run_id for reconnect after page reload.
- GET /conversations/{id}/messages → `{id,role,content,citations:[],created_at,run_id}[]`.
- POST /conversations/{id}/runs `{message,attachment_ids:[]}` → `{id,status}` (202); forbidden extra keys.
- GET /runs/{id} → `{id,status,error?,approvals:[],created_at}`; POST /runs/{id}/cancel.
- GET /runs/{id}/events?after=0 → SSE id sequence, event generic type, data JSON. Types status `{status,message}`, delta `{text}`, citation `{id,title,locator,excerpt}`, approval `{id,title,summary,parameters,expires_at}`, result `{message}`, done `{status}`, error `{message}`. Never raw framework events. Replay by Last-Event-ID or after.
- POST /approvals/{id}/decision `{approved:boolean}`; only owner, durable once-only decision.
- POST /messages/{id}/feedback `{rating:1|-1,comment:''}`.
- POST /admin/agents/{id}/debug `{message}` → same run shape; uses private admin-owned conversation and draft.

## Tools (next task)

- GET/POST /admin/tools; PATCH/DELETE /admin/tools/{id}; fields `{id,name,title,description,transport:'http'|'mcp',endpoint,method:'POST',mcp_tool_name:'',input_schema:{},fixed_arguments:{},headers:{},effect:'read'|'write',policy:'auto'|'confirm',allowed_group_ids:[],timeout_seconds:30,idempotency_header:'',enabled:true}`. Headers are encrypted secrets, GET returns only has_headers.
- POST /admin/tools/discover `{endpoint,headers:{}}` → discovered tool list (not automatically registered).
- GET /admin/tool-calls?status=outcome_unknown → redacted call summaries for reconciliation.
- POST /admin/tool-calls/{id}/resolve `{outcome:'succeeded'|'failed',note}` → reconciliation audit, never reexecutes.

## Knowledge / files

- GET/POST /admin/knowledge-bases `{name,description,embedding_model_id}`; PATCH/DELETE /admin/knowledge-bases/{id}.
- GET/POST /admin/knowledge-bases/{id}/documents (POST multipart file); DELETE /admin/documents/{id}; POST .../retry; POST /admin/knowledge-bases/{id}/search `{query}`.
- POST /files multipart conversation_id,file → `{id,name,status,error?}`; GET /files?conversation_id=...; GET /files/{id}; GET /files/{id}/download; DELETE /files/{id}.
- GET /citations/{id} → authorized `{id,title,locator,excerpt}`.

同名知识库上传保留文档 ID、增加 revision，旧来源立即退出检索；只允许重试 failed 文档。所有文件响应白名单为 `{id,name,status,error}`，error 可为 null，状态为 queued/processing/indexing/ready/failed。知识库仍有任意文档时切换 embedding_model_id 返回409；Agent 草稿或保留版本引用的知识库不能删除。检索测试返回片段数组，其中预览 id 是 chunk ID，不能用于成员引用接口。

成员引用必须属于本人且来源仍被当前 Agent 授权。来源删除、替换或撤权返回404；关联助手历史回答和事件中的旧内容会投影为不可用提示，不进入后续模型上下文。普通无引用回答不受影响。

模型管理 DTO 增加 `capabilities` 和 `tested_at`。对话模型能力键为 streaming/tool_calling；嵌入模型为 embedding/embedding_dimensions。未测试的键缺省；可选工具调用未通过不会否定已验证的纯对话能力。修改端点、模型标识、用途或凭证会清除测试结果。
