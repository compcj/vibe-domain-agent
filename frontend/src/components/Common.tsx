import { useEffect, useRef, type ReactNode } from "react";
import { LoaderCircle, X, Hexagon } from "lucide-react";

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand">
      <span className="brand-mark">
        <Hexagon size={25} strokeWidth={1.8} />
        <span />
      </span>
      {!compact && (
        <div>
          <strong>VIBE DOMAIN</strong>
          <small>组织智能工作台</small>
        </div>
      )}
    </div>
  );
}
export function Spinner({ label = "加载中" }: { label?: string }) {
  return (
    <div className="loading">
      <LoaderCircle className="spin" size={20} />
      <span>{label}</span>
    </div>
  );
}
export function Empty({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-orbit">
        <Hexagon size={30} strokeWidth={1} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? "wide" : ""}`}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭" onClick={onClose}>
          <X size={20} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function StatusTag({ status }: { status: string }) {
  const labels: Record<string, string> = {
    ready: "可用",
    completed: "已完成",
    queued: "排队中",
    processing: "处理中",
    indexing: "索引中",
    running: "执行中",
    failed: "失败",
    cancelled: "已停止",
    expired: "确认已过期",
    awaiting_confirmation: "等待确认",
    outcome_unknown: "结果待核实",
  };
  return (
    <span
      className={`tag ${status === "failed" ? "danger" : status === "outcome_unknown" || status === "awaiting_confirmation" ? "warning" : ""}`}
    >
      {labels[status] ?? status}
    </span>
  );
}
