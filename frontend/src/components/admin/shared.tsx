import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { Field } from "../Common";

export function useResource<T>(path: string) {
  const [data, setData] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api<T[]>(path));
      setError("");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, loading, error, setError, reload };
}
export function Toolbar({
  title,
  description,
  action,
  onAdd,
  onRefresh,
}: {
  title: string;
  description: string;
  action?: string;
  onAdd?: () => void;
  onRefresh: () => void;
}) {
  return (
    <div className="section-heading">
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      <div className="toolbar-actions">
        <button
          className="button secondary"
          aria-label="刷新列表"
          onClick={onRefresh}
        >
          <RefreshCw size={16} />
        </button>
        {onAdd && (
          <button className="button primary" onClick={onAdd}>
            <Plus size={16} />
            {action ?? "新建"}
          </button>
        )}
      </div>
    </div>
  );
}
export function GroupPicker({
  groups,
  value,
  onChange,
  label = "授权用户组",
}: {
  groups: { id: string; name: string }[];
  value: string[];
  onChange: (value: string[]) => void;
  label?: string;
}) {
  return (
    <fieldset className="choice-field">
      <legend>{label}</legend>
      {groups.length ? (
        <div className="check-grid">
          {groups.map((group) => (
            <label key={group.id}>
              <input
                type="checkbox"
                checked={value.includes(group.id)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...value, group.id]
                      : value.filter((id) => id !== group.id),
                  )
                }
              />
              {group.name}
            </label>
          ))}
        </div>
      ) : (
        <p className="muted">暂无可选项，请先在对应管理页面创建。</p>
      )}
      {label === "授权用户组" && (
        <small>至少授予一个用户组，成员才能使用。</small>
      )}
    </fieldset>
  );
}
export function JsonField({
  label,
  value,
  onChange,
  hint,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  required?: boolean;
}) {
  return (
    <Field label={label} hint={hint}>
      <textarea
        className="code-input"
        rows={5}
        required={required}
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}
export function parseObject(
  text: string,
  label: string,
): Record<string, unknown> {
  const value: unknown = JSON.parse(text || "{}");
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label}必须是 JSON 对象`);
  return value as Record<string, unknown>;
}
export function FormActions({
  busy,
  onClose,
  children,
}: {
  busy: boolean;
  onClose: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="form-actions">
      <button
        type="button"
        className="button secondary"
        onClick={onClose}
        disabled={busy}
      >
        取消
      </button>
      <button className="button primary" type="submit" disabled={busy}>
        {busy ? "保存中…" : (children ?? "保存配置")}
      </button>
    </div>
  );
}
export function EnabledToggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="check-inline">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      启用
    </label>
  );
}
