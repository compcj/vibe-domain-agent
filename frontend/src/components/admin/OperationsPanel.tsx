import { useCallback, useEffect, useState } from "react";
import { Activity, CheckCircle2, Clock3, AlertCircle } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { Field, Modal, Spinner } from "../Common";
import { FormActions, Toolbar } from "./shared";

interface Audit {
  id: string;
  action: string;
  actor_id?: string;
  resource_type: string;
  resource_id?: string;
  created_at: string;
}
interface PendingCall {
  id: string;
  title?: string;
  tool_name?: string;
  run_id: string;
  created_at: string;
}
export function OperationsPanel() {
  const [metrics, setMetrics] = useState<Record<string, unknown>>({});
  const [audit, setAudit] = useState<Audit[]>([]);
  const [calls, setCalls] = useState<PendingCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<PendingCall | null>(null);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [m, a, c] = await Promise.all([
        api<Record<string, unknown>>("/admin/metrics"),
        api<Audit[]>("/admin/audit"),
        api<PendingCall[]>("/admin/tool-calls?status=outcome_unknown"),
      ]);
      setMetrics(m);
      setAudit(a);
      setCalls(c);
      setError("");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);
  const value = (key: string) =>
    typeof metrics[key] === "number"
      ? Number(metrics[key]).toLocaleString()
      : "—";
  return (
    <>
      <Toolbar
        title="运行与审计"
        description="查看使用情况与操作记录，核实结果不明确的业务调用。"
        onRefresh={() => void reload()}
      />
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <Spinner />
      ) : (
        <>
          <div className="metrics-grid">
            {[
              { key: "total_runs", title: "累计运行", icon: Activity },
              { key: "completed_runs", title: "已完成", icon: CheckCircle2 },
              { key: "active_runs", title: "进行中", icon: Clock3 },
              { key: "failed_runs", title: "失败运行", icon: AlertCircle },
            ].map(({ key, title, icon: Icon }) => (
              <article key={key} className="metric-card">
                <span>
                  <Icon size={17} />
                  {title}
                </span>
                <strong>{value(key)}</strong>
              </article>
            ))}
          </div>
          <section className="admin-section">
            <h3>
              待核实的业务结果 <span className="count">{calls.length}</span>
            </h3>
            <p className="muted">
              请先在业务系统核实实际结果，再记录结论。此操作不会再次执行工具。
            </p>
            {calls.map((call) => (
              <div className="document-row" key={call.id}>
                <AlertCircle size={18} />
                <div>
                  <strong>{call.title || call.tool_name || "业务操作"}</strong>
                  <small>调用 {call.id}</small>
                </div>
                <button
                  className="button secondary"
                  onClick={() => setSelected(call)}
                >
                  记录核实结果
                </button>
              </div>
            ))}
            {!calls.length && (
              <p className="empty-line">目前没有待核实的操作。</p>
            )}
          </section>
          <section className="admin-section">
            <h3>操作审计</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>操作</th>
                    <th>资源</th>
                    <th>发起成员</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((entry) => (
                    <tr key={entry.id}>
                      <td>{new Date(entry.created_at).toLocaleString()}</td>
                      <td>{entry.action}</td>
                      <td>
                        {entry.resource_type}
                        <small>{entry.resource_id}</small>
                      </td>
                      <td className="mono">{entry.actor_id ?? "系统"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!audit.length && <p className="empty-line">暂无操作记录。</p>}
            </div>
          </section>
        </>
      )}
      {selected && (
        <ResolveCall
          call={selected}
          onClose={() => setSelected(null)}
          onSaved={async () => {
            setSelected(null);
            await reload();
          }}
        />
      )}
    </>
  );
}
function ResolveCall({
  call,
  onClose,
  onSaved,
}: {
  call: PendingCall;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [outcome, setOutcome] = useState("succeeded");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    try {
      await api(`/admin/tool-calls/${call.id}/resolve`, {
        method: "POST",
        body: { outcome, note },
      });
      await onSaved();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="记录业务核实结果" onClose={onClose}>
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <p className="muted">
          仅在业务系统查证后提交。结论进入审计，成员可看到任务结果。
        </p>
        <Field label="已核实的结果">
          <select
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
          >
            <option value="succeeded">业务操作已成功</option>
            <option value="failed">业务操作未成功</option>
          </select>
        </Field>
        <Field label="核实说明">
          <textarea
            required
            minLength={2}
            maxLength={1000}
            rows={4}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="记录业务记录编号及核实依据，不填写凭证"
          />
        </Field>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <FormActions busy={busy} onClose={onClose}>
          记录结果
        </FormActions>
      </form>
    </Modal>
  );
}
