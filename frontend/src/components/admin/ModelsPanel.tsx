import { useState } from "react";
import { Cpu, Pencil, PlugZap, Trash2 } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import type { ModelConnection } from "../../types";
import { Empty, Field, Modal, Spinner } from "../Common";
import { EnabledToggle, FormActions, Toolbar, useResource } from "./shared";

export function ModelsPanel() {
  const resource = useResource<ModelConnection>("/admin/models");
  const [editing, setEditing] = useState<ModelConnection | null | undefined>();
  const [testing, setTesting] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  async function test(model: ModelConnection) {
    setTesting(model.id);
    try {
      const result = await api<{ ok: boolean; message: string }>(
        `/admin/models/${model.id}/test`,
        { method: "POST" },
      );
      setNotice(`${model.name}：${result.message}`);
    } catch (failure) {
      resource.setError(errorMessage(failure));
    } finally {
      setTesting(null);
    }
  }
  async function remove(model: ModelConnection) {
    if (
      !window.confirm(
        `删除模型连接“${model.name}”？被 Agent 引用的连接不能删除。`,
      )
    )
      return;
    try {
      await api(`/admin/models/${model.id}`, { method: "DELETE" });
      await resource.reload();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    }
  }
  return (
    <>
      <Toolbar
        title="模型连接"
        description="集中管理对话与嵌入能力，凭证仅保存在服务端。"
        action="添加连接"
        onAdd={() => setEditing(null)}
        onRefresh={() => void resource.reload()}
      />
      {resource.error && (
        <p className="error-banner" role="alert">
          {resource.error}
        </p>
      )}
      {notice && (
        <p className="notice-banner" role="status">
          {notice}
        </p>
      )}
      {resource.loading ? (
        <Spinner />
      ) : resource.data.length ? (
        <div className="resource-grid">
          {resource.data.map((model) => (
            <article className="resource-card" key={model.id}>
              <div className="resource-card-heading">
                <span className="resource-icon">
                  <Cpu size={21} />
                </span>
                <span className={`tag ${model.enabled ? "" : "neutral"}`}>
                  {model.enabled ? "已启用" : "已停用"}
                </span>
              </div>
              <h3>{model.name}</h3>
              <p className="mono">{model.model}</p>
              <p className="muted truncate">{model.base_url}</p>
              <div className="resource-meta">
                <span>{model.kind === "chat" ? "对话模型" : "嵌入模型"}</span>
                <span>{model.has_api_key ? "已保存凭证" : "无凭证"}</span>
              </div>
              <div className="card-actions">
                <button
                  className="button text"
                  disabled={testing === model.id}
                  onClick={() => void test(model)}
                >
                  <PlugZap size={15} />
                  {testing === model.id ? "测试中" : "测试连接"}
                </button>
                <button
                  className="icon-button"
                  aria-label={`编辑 ${model.name}`}
                  onClick={() => setEditing(model)}
                >
                  <Pencil size={16} />
                </button>
                <button
                  className="icon-button danger-text"
                  aria-label={`删除 ${model.name}`}
                  onClick={() => void remove(model)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="连接你的第一个模型"
          description="添加兼容的模型服务，再为不同 Agent 分配能力。"
        />
      )}
      {editing !== undefined && (
        <ModelEditor
          model={editing}
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

function ModelEditor({
  model,
  onClose,
  onSaved,
}: {
  model: ModelConnection | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(model?.name ?? "");
  const [kind, setKind] = useState(model?.kind ?? "chat");
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? "");
  const [identifier, setIdentifier] = useState(model?.model ?? "");
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [enabled, setEnabled] = useState(model?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    try {
      await api(model ? `/admin/models/${model.id}` : "/admin/models", {
        method: model ? "PATCH" : "POST",
        body: {
          name,
          kind,
          base_url: baseUrl,
          model: identifier,
          enabled,
          ...(!model || key || clearKey
            ? { api_key: clearKey ? "" : key }
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
    <Modal title={model ? "编辑模型连接" : "添加模型连接"} onClose={onClose}>
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Field label="连接名称">
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：组织通用对话模型"
          />
        </Field>
        <div className="form-columns">
          <Field label="模型用途">
            <select
              value={kind}
              onChange={(event) =>
                setKind(event.target.value as "chat" | "embedding")
              }
            >
              <option value="chat">对话</option>
              <option value="embedding">文本嵌入</option>
            </select>
          </Field>
          <Field label="模型标识">
            <input
              required
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              placeholder="服务端提供的模型名称"
            />
          </Field>
        </div>
        <Field
          label="API Base URL"
          hint="兼容接口根地址，例如 https://models.example.com/v1"
        >
          <input
            type="url"
            required
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
          />
        </Field>
        <Field
          label="API Key"
          hint={
            model ? "留空保留已保存的凭证。" : "无需认证的内网服务可以留空。"
          }
        >
          <input
            type="password"
            autoComplete="new-password"
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
        </Field>
        {model?.has_api_key && (
          <label className="check-inline">
            <input
              type="checkbox"
              checked={clearKey}
              onChange={(event) => setClearKey(event.target.checked)}
            />
            清除已有凭证
          </label>
        )}
        <EnabledToggle checked={enabled} onChange={setEnabled} />
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <FormActions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}
