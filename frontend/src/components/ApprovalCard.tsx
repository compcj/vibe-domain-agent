import { useEffect, useRef, useState } from "react";
import { ShieldCheck } from "lucide-react";
import type { Approval } from "../types";
import { errorMessage } from "../lib/api";

export function ApprovalCard({
  approval,
  onDecide,
}: {
  approval: Approval;
  onDecide: (id: string, approved: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [decision, setDecision] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const submitted = useRef(false);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const expired = now >= Date.parse(approval.expires_at);
  async function decide(approved: boolean) {
    if (submitted.current || expired) return;
    submitted.current = true;
    setBusy(true);
    setError("");
    try {
      await onDecide(approval.id, approved);
      setDecision(approved);
    } catch (failure) {
      submitted.current = false;
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="approval-card" aria-label="业务操作确认">
      <div className="approval-title">
        <ShieldCheck size={19} />
        <strong>{approval.title}</strong>
        <span className="tag warning">需要确认</span>
      </div>
      <p>{approval.summary}</p>
      <dl>
        {Object.entries(approval.parameters).map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd>
          </div>
        ))}
      </dl>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {decision !== null ? (
        <p className="muted">
          {decision ? "已确认，正在处理" : "已拒绝此操作"}
        </p>
      ) : (
        <div className="approval-actions">
          <button
            type="button"
            className="button primary"
            disabled={busy || expired}
            onClick={() => void decide(true)}
          >
            确认执行
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={busy || expired}
            onClick={() => void decide(false)}
          >
            拒绝
          </button>
          <small>{expired ? "确认已过期" : "请核对业务对象与参数"}</small>
        </div>
      )}
    </section>
  );
}
