import { useEffect, useState } from "react";
import { Pencil, Trash2, Wrench } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import type { Group, ToolDefinition } from "../../types";
import { Empty, Field, Modal, Spinner } from "../Common";
import {
  EnabledToggle,
  FormActions,
  GroupPicker,
  JsonField,
  parseObject,
  Toolbar,
  useResource,
} from "./shared";

type Discovered = {
  name: string;
  title?: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
  input_schema?: Record<string, unknown>;
};

export function ToolsPanel() {
  const resource = useResource<ToolDefinition>("/admin/tools");
  const [editing, setEditing] = useState<ToolDefinition | null | undefined>();
  async function remove(tool: ToolDefinition) {
    if (!window.confirm(`删除工具“${tool.title || tool.name}”？`)) return;
    try {
      await api(`/admin/tools/${tool.id}`, { method: "DELETE" });
      await resource.reload();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    }
  }
  return (
    <>
      <Toolbar
        title="工具与业务连接"
        description="工具授权独立于 Agent 授权，写操作默认需要成员确认。"
        action="注册工具"
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
          {resource.data.map((tool) => (
            <article className="resource-card" key={tool.id}>
              <div className="resource-card-heading">
                <span className="resource-icon">
                  <Wrench size={21} />
                </span>
                <span
                  className={`tag ${tool.effect === "write" ? "warning" : ""}`}
                >
                  {tool.effect === "write" ? "业务写入" : "只读查询"}
                </span>
              </div>
              <h3>{tool.title || tool.name}</h3>
              <p className="resource-description">
                {tool.description || "暂无工具描述"}
              </p>
              <div className="resource-meta">
                <span>
                  {tool.transport.toUpperCase()} ·{" "}
                  {tool.policy === "confirm" ? "需要确认" : "自动执行"}
                </span>
                <span>{tool.enabled ? "已启用" : "已停用"}</span>
              </div>
              <div className="card-actions">
                <span className="muted small">
                  {tool.allowed_group_ids.length} 个授权组
                </span>
                <button
                  className="icon-button"
                  aria-label={`编辑 ${tool.name}`}
                  onClick={() => setEditing(tool)}
                >
                  <Pencil size={16} />
                </button>
                <button
                  className="icon-button danger-text"
                  aria-label={`删除 ${tool.name}`}
                  onClick={() => void remove(tool)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="接入业务工具"
          description="注册 HTTP 或 MCP 工具，为助手增加可控的行动能力。"
        />
      )}
      {editing !== undefined && (
        <ToolEditor
          tool={editing}
          onClose={() => setEditing(undefined)}
          onSaved={async () => {
            setEditing(undefined);
            await resource.reload();
          }}
        />
      )}
    </>
  );
}

function ToolEditor({
  tool,
  onClose,
  onSaved,
}: {
  tool: ToolDefinition | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState({
    name: tool?.name ?? "",
    title: tool?.title ?? "",
    description: tool?.description ?? "",
    transport: tool?.transport ?? "http",
    endpoint: tool?.endpoint ?? "",
    method: tool?.method ?? "POST",
    mcp_tool_name: tool?.mcp_tool_name ?? "",
    effect: tool?.effect ?? "read",
    policy: tool?.policy ?? "auto",
    allowed_group_ids: tool?.allowed_group_ids ?? [],
    timeout_seconds: tool?.timeout_seconds ?? 30,
    idempotency_header: tool?.idempotency_header ?? "",
    enabled: tool?.enabled ?? true,
  });
  const [schema, setSchema] = useState(
    JSON.stringify(
      tool?.input_schema ?? {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
      null,
      2,
    ),
  );
  const [fixed, setFixed] = useState(
    JSON.stringify(tool?.fixed_arguments ?? {}, null, 2),
  );
  const [headers, setHeaders] = useState("");
  const [clearHeaders, setClearHeaders] = useState(false);
  const [groups, setGroups] = useState<Group[]>([]);
  const [discovered, setDiscovered] = useState<Discovered[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void api<Group[]>("/admin/groups")
      .then(setGroups)
      .catch((failure: unknown) => setError(errorMessage(failure)));
  }, []);
  function change<K extends keyof typeof form>(
    key: K,
    value: (typeof form)[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }
  async function discover() {
    setDiscovering(true);
    setError("");
    try {
      const result = await api<Discovered[] | { tools: Discovered[] }>(
        "/admin/tools/discover",
        {
          method: "POST",
          body: {
            endpoint: form.endpoint,
            headers: parseObject(headers, "认证头"),
          },
        },
      );
      setDiscovered(Array.isArray(result) ? result : result.tools);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setDiscovering(false);
    }
  }
  function selectDiscovered(name: string) {
    const selected = discovered.find((item) => item.name === name);
    if (!selected) return;
    setForm((current) => ({
      ...current,
      mcp_tool_name: selected.name,
      name: current.name || selected.name,
      title: current.title || selected.title || selected.name,
      description: selected.description ?? current.description,
    }));
    setSchema(
      JSON.stringify(
        selected.inputSchema ?? selected.input_schema ?? {},
        null,
        2,
      ),
    );
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api(tool ? `/admin/tools/${tool.id}` : "/admin/tools", {
        method: tool ? "PATCH" : "POST",
        body: {
          ...form,
          input_schema: parseObject(schema, "参数 Schema"),
          fixed_arguments: parseObject(fixed, "固定参数"),
          ...(!tool || headers || clearHeaders
            ? { headers: clearHeaders ? {} : parseObject(headers, "认证头") }
            : {}),
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
    <Modal title={tool ? "编辑工具" : "注册工具"} onClose={onClose} wide>
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="form-columns">
          <Field label="工具标识">
            <input
              required
              pattern="[a-zA-Z0-9_.-]+"
              value={form.name}
              onChange={(event) => change("name", event.target.value)}
              placeholder="例如 lookup_ticket"
            />
          </Field>
          <Field label="业务名称">
            <input
              required
              value={form.title}
              onChange={(event) => change("title", event.target.value)}
              placeholder="例如 查询工单"
            />
          </Field>
        </div>
        <Field label="工具描述">
          <textarea
            rows={2}
            required
            value={form.description}
            onChange={(event) => change("description", event.target.value)}
          />
        </Field>
        <div className="form-columns">
          <Field label="接入方式">
            <select
              value={form.transport}
              onChange={(event) =>
                change("transport", event.target.value as "http" | "mcp")
              }
            >
              <option value="http">HTTP 接口</option>
              <option value="mcp">远程 MCP（Streamable HTTP）</option>
            </select>
          </Field>
          {form.transport === "http" ? (
            <Field label="HTTP 方法">
              <select
                value={form.method}
                onChange={(event) => change("method", event.target.value)}
              >
                {["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => (
                  <option key={method}>{method}</option>
                ))}
              </select>
            </Field>
          ) : (
            <Field label="MCP 工具名称">
              <input
                required
                value={form.mcp_tool_name}
                onChange={(event) =>
                  change("mcp_tool_name", event.target.value)
                }
              />
            </Field>
          )}
        </div>
        <Field
          label="固定服务地址"
          hint="由管理员指定，不允许模型或成员覆盖地址。"
        >
          <input
            type="url"
            required
            value={form.endpoint}
            onChange={(event) => change("endpoint", event.target.value)}
          />
        </Field>
        <JsonField
          label="认证请求头（加密保存）"
          value={headers}
          onChange={setHeaders}
          hint={
            tool
              ? "留空保留已有凭证。发现工具时请重新填写必要的认证头。"
              : "JSON 对象，例如服务要求的 Authorization 头。无需认证可留空。"
          }
        />
        {tool?.has_headers && (
          <label className="check-inline">
            <input
              type="checkbox"
              checked={clearHeaders}
              onChange={(event) => setClearHeaders(event.target.checked)}
            />
            清除已有认证头
          </label>
        )}
        {form.transport === "mcp" && (
          <div className="discover-tools">
            <button
              className="button secondary"
              type="button"
              disabled={discovering || !form.endpoint}
              onClick={() => void discover()}
            >
              {discovering ? "读取中…" : "读取远程工具列表"}
            </button>
            {discovered.length > 0 && (
              <select
                aria-label="选择远程工具"
                defaultValue=""
                onChange={(event) => selectDiscovered(event.target.value)}
              >
                <option value="" disabled>
                  选择要注册的工具
                </option>
                {discovered.map((item) => (
                  <option value={item.name} key={item.name}>
                    {item.title || item.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}
        <div className="form-columns">
          <Field label="操作性质">
            <select
              value={form.effect}
              onChange={(event) => {
                const effect = event.target.value as "read" | "write";
                setForm((current) => ({
                  ...current,
                  effect,
                  policy: effect === "write" ? "confirm" : "auto",
                }));
              }}
            >
              <option value="read">只读查询</option>
              <option value="write">写入业务系统</option>
            </select>
          </Field>
          <Field label="执行策略">
            <select
              value={form.policy}
              onChange={(event) =>
                change("policy", event.target.value as "auto" | "confirm")
              }
            >
              <option value="confirm">成员确认后执行</option>
              <option value="auto">自动执行</option>
            </select>
          </Field>
        </div>
        {form.effect === "write" && form.policy === "auto" && (
          <p className="notice-banner">
            获授权成员使用该工具时，符合参数约束的操作会直接执行。
          </p>
        )}
        <JsonField
          label="参数 JSON Schema"
          required
          value={schema}
          onChange={setSchema}
        />
        <JsonField
          label="服务端固定参数"
          value={fixed}
          onChange={setFixed}
          hint="模型和成员不能改变这些参数。不要在此填写凭证。"
        />
        <GroupPicker
          groups={groups}
          value={form.allowed_group_ids}
          onChange={(value) => change("allowed_group_ids", value)}
        />
        <div className="form-columns">
          <Field label="超时（秒）">
            <input
              type="number"
              min={1}
              max={120}
              value={form.timeout_seconds}
              onChange={(event) =>
                change("timeout_seconds", Number(event.target.value))
              }
            />
          </Field>
          <Field label="上游幂等请求头" hint="仅在上游明确支持时填写。">
            <input
              value={form.idempotency_header}
              onChange={(event) =>
                change("idempotency_header", event.target.value)
              }
              placeholder="例如 Idempotency-Key"
            />
          </Field>
        </div>
        <EnabledToggle
          checked={form.enabled}
          onChange={(value) => change("enabled", value)}
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <FormActions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}
