import { useEffect, useState } from "react";
import { BookOpen, History, Pencil, Play, Upload } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { watchRun } from "../../lib/stream";
import type {
  AdminAgent,
  AgentVersion,
  Approval,
  Draft,
  Group,
  KnowledgeBase,
  ModelConnection,
  Run,
  ToolDefinition,
} from "../../types";
import { ApprovalCard } from "../ApprovalCard";
import { Empty, Field, Modal, Spinner } from "../Common";
import {
  EnabledToggle,
  FormActions,
  GroupPicker,
  Toolbar,
  useResource,
} from "./shared";

export function AgentsPanel({ onChanged }: { onChanged: () => void }) {
  const resource = useResource<AdminAgent>("/admin/agents");
  const [editing, setEditing] = useState<AdminAgent | null | undefined>();
  const [versions, setVersions] = useState<{
    agent: AdminAgent;
    items: AgentVersion[];
  } | null>(null);
  const [debug, setDebug] = useState<AdminAgent | null>(null);
  const [busyId, setBusyId] = useState("");
  async function publish(agent: AdminAgent) {
    setBusyId(agent.id);
    try {
      await api(`/admin/agents/${agent.id}/publish`, { method: "POST" });
      await resource.reload();
      onChanged();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    } finally {
      setBusyId("");
    }
  }
  async function showVersions(agent: AdminAgent) {
    try {
      setVersions({
        agent,
        items: await api<AgentVersion[]>(`/admin/agents/${agent.id}/versions`),
      });
    } catch (failure) {
      resource.setError(errorMessage(failure));
    }
  }
  async function rollback(version: AgentVersion) {
    if (
      !versions ||
      !window.confirm("将后续运行切换到此版本？正在执行的任务保留原快照。")
    )
      return;
    try {
      await api(`/admin/agents/${versions.agent.id}/rollback`, {
        method: "POST",
        body: { version_id: version.id },
      });
      setVersions(null);
      await resource.reload();
      onChanged();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    }
  }
  return (
    <>
      <Toolbar
        title="Agent 管理"
        description="定义业务能力，测试后发布给指定用户组。"
        action="创建 Agent"
        onAdd={() => setEditing(null)}
        onRefresh={() => void resource.reload()}
      />
      {resource.error && (
        <p className="error-banner" role="alert">
          {resource.error}
        </p>
      )}
      {resource.loading ? (
        <Spinner />
      ) : resource.data.length ? (
        <div className="resource-grid">
          {resource.data.map((agent) => (
            <article className="resource-card" key={agent.id}>
              <div className="resource-card-heading">
                <span className="resource-icon">
                  <BookOpen size={22} />
                </span>
                <span
                  className={`tag ${agent.enabled && agent.published_version_id ? "" : "neutral"}`}
                >
                  {!agent.enabled
                    ? "已停用"
                    : agent.published_version_id
                      ? "已发布"
                      : "草稿"}
                </span>
              </div>
              <h3>{agent.name}</h3>
              <p className="resource-description">
                {agent.description || "尚未添加业务介绍"}
              </p>
              <div className="resource-meta">
                <span>{agent.allowed_group_ids.length} 个授权用户组</span>
                <span>{agent.draft.tool_ids.length} 个工具</span>
              </div>
              <div className="card-actions">
                <button
                  className="button text"
                  disabled={busyId === agent.id || !agent.enabled}
                  onClick={() => void publish(agent)}
                >
                  <Upload size={14} />
                  发布
                </button>
                <button
                  className="icon-button"
                  aria-label={`调试 ${agent.name}`}
                  onClick={() => setDebug(agent)}
                >
                  <Play size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label={`${agent.name} 版本记录`}
                  onClick={() => void showVersions(agent)}
                >
                  <History size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label={`编辑 ${agent.name}`}
                  onClick={() => setEditing(agent)}
                >
                  <Pencil size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="把领域知识变成一位助手"
          description="创建 Agent，配置模型与知识，再发布给组织成员。"
        />
      )}
      {editing !== undefined && (
        <AgentEditor
          agent={editing}
          onClose={() => setEditing(undefined)}
          onSaved={async () => {
            setEditing(undefined);
            await resource.reload();
            onChanged();
          }}
        />
      )}
      {versions && (
        <Modal
          title={`${versions.agent.name} · 版本记录`}
          onClose={() => setVersions(null)}
        >
          <div className="modal-form">
            {versions.items.length ? (
              versions.items.map((version) => (
                <div className="version-row" key={version.id}>
                  <div>
                    <strong>
                      版本{" "}
                      {version.version ??
                        version.version_number ??
                        version.id.slice(0, 8)}
                    </strong>
                    <small>
                      {new Date(version.created_at).toLocaleString()}
                    </small>
                  </div>
                  <button
                    className="button secondary"
                    onClick={() => void rollback(version)}
                  >
                    恢复此版本
                  </button>
                </div>
              ))
            ) : (
              <p className="muted">还没有已发布版本。</p>
            )}
          </div>
        </Modal>
      )}
      {debug && <DebugAgent agent={debug} onClose={() => setDebug(null)} />}
    </>
  );
}

function AgentEditor({
  agent,
  onClose,
  onSaved,
}: {
  agent: AdminAgent | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(agent?.name ?? "");
  const [description, setDescription] = useState(agent?.description ?? "");
  const [examples, setExamples] = useState(agent?.examples.join("\n") ?? "");
  const [enabled, setEnabled] = useState(agent?.enabled ?? true);
  const [groupIds, setGroupIds] = useState(agent?.allowed_group_ids ?? []);
  const [draft, setDraft] = useState<Draft>(
    agent?.draft ?? {
      model_id: "",
      system_prompt: "",
      temperature: 0.7,
      max_tokens: 4096,
      tool_ids: [],
      knowledge_base_ids: [],
      max_steps: 20,
      timeout_seconds: 300,
    },
  );
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeBase[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void Promise.all([
      api<ModelConnection[]>("/admin/models"),
      api<Group[]>("/admin/groups"),
      api<ToolDefinition[]>("/admin/tools"),
      api<KnowledgeBase[]>("/admin/knowledge-bases"),
    ])
      .then(([m, g, t, k]) => {
        setModels(m);
        setGroups(g);
        setTools(t);
        setKnowledge(k);
      })
      .catch((failure: unknown) => setError(errorMessage(failure)));
  }, []);
  function change<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }
  async function save() {
    setBusy(true);
    try {
      await api(agent ? `/admin/agents/${agent.id}` : "/admin/agents", {
        method: agent ? "PATCH" : "POST",
        body: {
          name,
          description,
          examples: examples
            .split("\n")
            .map((value) => value.trim())
            .filter(Boolean),
          enabled,
          allowed_group_ids: groupIds,
          draft,
        },
      });
      await onSaved();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={agent ? "编辑 Agent 草稿" : "创建 Agent"}
      onClose={onClose}
      wide
    >
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="form-columns">
          <Field label="名称">
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="对话模型">
            <select
              required
              value={draft.model_id}
              onChange={(event) => change("model_id", event.target.value)}
            >
              <option value="">选择模型连接</option>
              {models
                .filter((model) => model.kind === "chat")
                .map((model) => (
                  <option
                    key={model.id}
                    value={model.id}
                    disabled={!model.enabled}
                  >
                    {model.name}
                  </option>
                ))}
            </select>
          </Field>
        </div>
        <Field label="业务介绍">
          <textarea
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="说明这位助手能为成员完成什么工作"
          />
        </Field>
        <Field label="示例问题" hint="每行一个，展示给成员。">
          <textarea
            rows={2}
            value={examples}
            onChange={(event) => setExamples(event.target.value)}
          />
        </Field>
        <Field
          label="系统提示词"
          hint="仅管理员配置，不放入 API 密钥或服务凭证。"
        >
          <textarea
            required
            rows={7}
            value={draft.system_prompt}
            onChange={(event) => change("system_prompt", event.target.value)}
          />
        </Field>
        <div className="form-columns">
          <Field label="Temperature">
            <input
              type="number"
              min={0}
              max={2}
              step={0.1}
              value={draft.temperature}
              onChange={(event) =>
                change("temperature", Number(event.target.value))
              }
            />
          </Field>
          <Field label="输出 Token 上限">
            <input
              type="number"
              min={1}
              max={32768}
              value={draft.max_tokens}
              onChange={(event) =>
                change("max_tokens", Number(event.target.value))
              }
            />
          </Field>
        </div>
        <GroupPicker groups={groups} value={groupIds} onChange={setGroupIds} />
        <GroupPicker
          groups={tools
            .filter((tool) => tool.enabled)
            .map((tool) => ({ id: tool.id, name: tool.title || tool.name }))}
          value={draft.tool_ids}
          onChange={(value) => change("tool_ids", value)}
          label="可用工具（仍需成员拥有工具授权）"
        />
        <GroupPicker
          groups={knowledge}
          value={draft.knowledge_base_ids}
          onChange={(value) => change("knowledge_base_ids", value)}
          label="绑定知识库（资料对 Agent 授权成员可见）"
        />
        <div className="form-columns">
          <Field label="最大循环轮数">
            <input
              type="number"
              min={1}
              max={20}
              value={draft.max_steps}
              onChange={(event) =>
                change("max_steps", Number(event.target.value))
              }
            />
          </Field>
          <Field label="执行预算（秒）">
            <input
              type="number"
              min={10}
              max={300}
              value={draft.timeout_seconds}
              onChange={(event) =>
                change("timeout_seconds", Number(event.target.value))
              }
            />
          </Field>
        </div>
        <EnabledToggle checked={enabled} onChange={setEnabled} />
        <p className="muted small">
          保存只更新草稿，发布后才影响成员的新运行。停用会立即阻止新的执行。
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <FormActions busy={busy} onClose={onClose}>
          保存草稿
        </FormActions>
      </form>
    </Modal>
  );
}

function DebugAgent({
  agent,
  onClose,
}: {
  agent: AdminAgent;
  onClose: () => void;
}) {
  const [message, setMessage] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  useEffect(() => {
    if (!run) return;
    const controller = new AbortController();
    void watchRun(
      run.id,
      (event) => {
        if (event.type === "delta")
          setOutput((value) => value + String(event.data.text));
        if (event.type === "approval")
          setApprovals((value) => [
            ...value,
            event.data as unknown as Approval,
          ]);
        if (event.type === "error") setError(String(event.data.message));
        if (event.type === "done") setBusy(false);
      },
      controller.signal,
    ).catch((failure: unknown) => {
      if (!controller.signal.aborted) {
        setError(errorMessage(failure));
        setBusy(false);
      }
    });
    return () => controller.abort();
  }, [run?.id]);
  async function start() {
    if (busy) return;
    setBusy(true);
    setOutput("");
    setError("");
    setApprovals([]);
    try {
      setRun(
        await api<Run>(`/admin/agents/${agent.id}/debug`, {
          method: "POST",
          body: { message },
        }),
      );
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }
  function close() {
    if (
      busy &&
      !window.confirm("调试仍在运行。关闭窗口不会撤销已提交的操作，是否关闭？")
    )
      return;
    onClose();
  }
  return (
    <Modal title={`${agent.name} · 草稿调试`} onClose={close} wide>
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <p className="muted">使用当前草稿，工具仍遵守执行策略和授权。</p>
        <Field label="测试问题">
          <textarea
            required
            rows={3}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
        </Field>
        <button className="button primary" disabled={busy}>
          {busy ? "执行中…" : "运行调试"}
        </button>
        {output && <div className="debug-output">{output}</div>}
        {approvals.map((approval) => (
          <ApprovalCard
            key={approval.id}
            approval={approval}
            onDecide={async (id, approved) => {
              await api(`/approvals/${id}/decision`, {
                method: "POST",
                body: { approved },
              });
            }}
          />
        ))}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}
