import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  FileText,
  Pencil,
  RotateCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import type {
  Citation,
  DocumentRecord,
  KnowledgeBase,
  ModelConnection,
} from "../../types";
import { Empty, Field, Modal, Spinner, StatusTag } from "../Common";
import { FormActions, Toolbar, useResource } from "./shared";

export function KnowledgePanel() {
  const resource = useResource<KnowledgeBase>("/admin/knowledge-bases");
  const [editing, setEditing] = useState<KnowledgeBase | null | undefined>();
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  async function remove(item: KnowledgeBase) {
    if (!window.confirm(`删除知识库“${item.name}”及其文档？`)) return;
    try {
      await api(`/admin/knowledge-bases/${item.id}`, { method: "DELETE" });
      await resource.reload();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    }
  }
  return (
    <>
      <Toolbar
        title="知识库"
        description="维护助手的参考资料，索引完成后即可用于回答。"
        action="创建知识库"
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
          {resource.data.map((item) => (
            <article className="resource-card" key={item.id}>
              <div className="resource-icon">
                <BookOpen size={23} />
              </div>
              <h3>{item.name}</h3>
              <p className="resource-description">
                {item.description || "组织参考资料"}
              </p>
              <div className="card-actions">
                <button
                  className="button text"
                  onClick={() => setSelected(item)}
                >
                  管理文档与检索
                </button>
                <button
                  className="icon-button"
                  aria-label={`编辑 ${item.name}`}
                  onClick={() => setEditing(item)}
                >
                  <Pencil size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label={`删除 ${item.name}`}
                  onClick={() => void remove(item)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="建立组织的知识来源"
          description="上传文档，绑定到 Agent，让回答有据可循。"
        />
      )}
      {editing !== undefined && (
        <KnowledgeEditor
          item={editing}
          onClose={() => setEditing(undefined)}
          onSaved={async () => {
            setEditing(undefined);
            await resource.reload();
          }}
        />
      )}
      {selected && (
        <Documents item={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}

function KnowledgeEditor({
  item,
  onClose,
  onSaved,
}: {
  item: KnowledgeBase | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(item?.name ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [model, setModel] = useState(item?.embedding_model_id ?? "");
  const models = useResource<ModelConnection>("/admin/models");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    try {
      await api(
        item ? `/admin/knowledge-bases/${item.id}` : "/admin/knowledge-bases",
        {
          method: item ? "PATCH" : "POST",
          body: { name, description, embedding_model_id: model },
        },
      );
      await onSaved();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={item ? "编辑知识库" : "创建知识库"} onClose={onClose}>
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Field label="名称">
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="用途说明">
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <Field
          label="嵌入模型"
          hint="更换前先删除库内文档，切换后重新上传索引。"
        >
          <select
            required
            value={model}
            onChange={(event) => setModel(event.target.value)}
          >
            <option value="">选择嵌入模型</option>
            {models.data
              .filter((entry) => entry.kind === "embedding")
              .map((entry) => (
                <option
                  disabled={!entry.enabled}
                  key={entry.id}
                  value={entry.id}
                >
                  {entry.name}
                </option>
              ))}
          </select>
        </Field>
        {(error || models.error) && (
          <p className="error" role="alert">
            {error || models.error}
          </p>
        )}
        <FormActions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}

function Documents({
  item,
  onClose,
}: {
  item: KnowledgeBase;
  onClose: () => void;
}) {
  const resource = useResource<DocumentRecord>(
    `/admin/knowledge-bases/${item.id}/documents`,
  );
  const upload = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Citation[] | null>(null);
  const [busy, setBusy] = useState(false);
  const processing = resource.data.some((doc) =>
    ["queued", "processing", "indexing"].includes(doc.status),
  );
  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => void resource.reload(), 2500);
    return () => clearInterval(timer);
  }, [processing, resource.reload]);
  async function add(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const file of Array.from(files)) {
        if (file.size > 20 * 1024 * 1024)
          throw new Error(`${file.name} 超过 20 MB`);
        const form = new FormData();
        form.set("file", file);
        await api(`/admin/knowledge-bases/${item.id}/documents`, {
          method: "POST",
          body: form,
        });
      }
      await resource.reload();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    } finally {
      setBusy(false);
      if (upload.current) upload.current.value = "";
    }
  }
  async function remove(doc: DocumentRecord) {
    if (!window.confirm(`删除文档“${doc.name}”？后续检索将不再使用。`)) return;
    try {
      await api(`/admin/documents/${doc.id}`, { method: "DELETE" });
      await resource.reload();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    }
  }
  async function retry(doc: DocumentRecord) {
    try {
      await api(`/admin/documents/${doc.id}/retry`, { method: "POST" });
      await resource.reload();
    } catch (failure) {
      resource.setError(errorMessage(failure));
    }
  }
  async function search() {
    setBusy(true);
    try {
      const found = await api<Citation[] | { results: Citation[] }>(
        `/admin/knowledge-bases/${item.id}/search`,
        { method: "POST", body: { query } },
      );
      setResults(Array.isArray(found) ? found : found.results);
    } catch (failure) {
      resource.setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={`${item.name} · 文档与检索`} onClose={onClose} wide>
      <div className="modal-form">
        <p className="muted">
          支持文本型 PDF、DOCX、TXT、Markdown、CSV、XLSX。每个文件最多 20 MB。同名上传会替换原文档并使旧引用失效。
        </p>
        <input
          ref={upload}
          type="file"
          hidden
          multiple
          accept=".pdf,.docx,.txt,.md,.markdown,.csv,.xlsx"
          onChange={(event) => void add(event.target.files)}
        />
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => upload.current?.click()}
        >
          <Upload size={16} />
          {busy ? "处理中…" : "上传文档"}
        </button>
        {resource.error && (
          <p className="error" role="alert">
            {resource.error}
          </p>
        )}
        <div className="document-list">
          {resource.data.map((doc) => (
            <div className="document-row" key={doc.id}>
              <FileText size={19} />
              <div>
                <strong>{doc.name}</strong>
                {doc.error && <small className="error">{doc.error}</small>}
              </div>
              <StatusTag status={doc.status} />
              {doc.status === "failed" && (
                <button
                  className="icon-button"
                  aria-label={`重试 ${doc.name}`}
                  onClick={() => void retry(doc)}
                >
                  <RotateCw size={16} />
                </button>
              )}
              <button
                className="icon-button"
                aria-label={`删除 ${doc.name}`}
                onClick={() => void remove(doc)}
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          {!resource.data.length && <p className="muted">还没有文档。</p>}
        </div>
        <form
          className="search-test"
          onSubmit={(event) => {
            event.preventDefault();
            void search();
          }}
        >
          <Field label="检索测试">
            <input
              required
              placeholder="输入业务问题，检查命中的来源"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Field>
          <button className="button primary" disabled={busy}>
            <Search size={16} />
            检索
          </button>
        </form>
        {results && (
          <div className="search-results">
            {results.length ? (
              results.map((result) => (
                <article key={result.id}>
                  <strong>{result.title}</strong>
                  <small>{result.locator}</small>
                  <p>{result.excerpt}</p>
                </article>
              ))
            ) : (
              <p className="muted">没有找到相关片段。</p>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
