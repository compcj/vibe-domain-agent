import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowUp,
  BookOpen,
  Check,
  Copy,
  FileText,
  Paperclip,
  Square,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, errorMessage } from "../lib/api";
import { watchRun } from "../lib/stream";
import type {
  AgentCard,
  Approval,
  Citation,
  Conversation,
  FileAsset,
  Message,
  Run,
} from "../types";
import { ApprovalCard } from "./ApprovalCard";
import { StatusTag } from "./Common";

const activeStates = ["queued", "running", "awaiting_confirmation"];

function MessageBody({ content }: { content: string }) {
  return (
    <div className="markdown">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: () => <span>[外部图片未加载]</span>,
        }}
      >
        {content}
      </Markdown>
    </div>
  );
}

function Sources({ citations }: { citations: Citation[] }) {
  return citations.length ? (
    <div className="citations">
      {citations.map((citation, index) => (
        <details key={citation.id}>
          <summary>
            <BookOpen size={13} />
            <span>
              {index + 1}. {citation.title}
            </span>
          </summary>
          <small>{citation.locator}</small>
          <p>{citation.excerpt}</p>
        </details>
      ))}
    </div>
  ) : null;
}

function MessageActions({ message }: { message: Message }) {
  const [rating, setRating] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  async function feedback(value: number) {
    try {
      await api(`/messages/${message.id}/feedback`, {
        method: "POST",
        body: { rating: value, comment: "" },
      });
      setRating(value);
      setError("");
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
    } catch {
      setError("无法访问剪贴板，请手动复制");
    }
  }
  return (
    <div className="message-actions">
      <button
        aria-label="复制回答"
        className="icon-button"
        onClick={() => void copy()}
      >
        {copied ? <Check size={15} /> : <Copy size={15} />}
      </button>
      <button
        aria-label="有帮助"
        aria-pressed={rating === 1}
        className="icon-button"
        onClick={() => void feedback(1)}
      >
        <ThumbsUp size={15} />
      </button>
      <button
        aria-label="需要改进"
        aria-pressed={rating === -1}
        className="icon-button"
        onClick={() => void feedback(-1)}
      >
        <ThumbsDown size={15} />
      </button>
      {error && (
        <span role="alert" className="error">
          {error}
        </span>
      )}
    </div>
  );
}

export function Chat({
  conversation,
  agent,
  onChanged,
}: {
  conversation: Conversation;
  agent?: AgentCard;
  onChanged: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [streamed, setStreamed] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [files, setFiles] = useState<FileAsset[]>([]);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const submitting = useRef(false);
  const busy = sending || (run !== null && activeStates.includes(run.status));
  const filesPending = files.some(
    (file) => !["ready", "completed", "failed"].includes(file.status),
  );

  async function refreshMessages() {
    setMessages(
      await api<Message[]>(`/conversations/${conversation.id}/messages`),
    );
  }
  async function refreshFiles() {
    setFiles(
      await api<FileAsset[]>(`/files?conversation_id=${conversation.id}`),
    );
  }

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      api<Message[]>(`/conversations/${conversation.id}/messages`, {
        signal: controller.signal,
      }),
      api<FileAsset[]>(`/files?conversation_id=${conversation.id}`, {
        signal: controller.signal,
      }),
      conversation.active_run_id
        ? api<Run>(`/runs/${conversation.active_run_id}`, {
            signal: controller.signal,
          })
        : Promise.resolve(null),
    ])
      .then(([history, attachments, active]) => {
        setMessages(history);
        setFiles(attachments);
        if (active) {
          setRun(active);
          setApprovals(active.approvals ?? []);
        }
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    return () => controller.abort();
  }, [conversation.id]);

  useEffect(() => {
    if (!filesPending) return;
    const timer = setInterval(() => {
      void refreshFiles().catch((failure: unknown) =>
        setError(errorMessage(failure)),
      );
    }, 2000);
    return () => clearInterval(timer);
  }, [filesPending, conversation.id]);

  useEffect(() => {
    if (!run || !activeStates.includes(run.status)) return;
    const controller = new AbortController();
    let finished = false;
    let approvalRequest = 0;
    void watchRun(
      run.id,
      (event) => {
        if (event.type === "delta")
          setStreamed((value) => value + String(event.data.text ?? ""));
        else if (event.type === "status") {
          setProgress(String(event.data.message ?? "正在处理"));
          setRun((value) =>
            value ? { ...value, status: String(event.data.status) } : value,
          );
        } else if (event.type === "citation") {
          const source = event.data as unknown as Citation;
          setCitations((value) =>
            value.some((item) => item.id === source.id)
              ? value
              : [...value, source],
          );
        } else if (event.type === "approval") {
          const sequence = ++approvalRequest;
          void api<Run>(`/runs/${run.id}`, { signal: controller.signal })
            .then((current) => {
              if (
                !finished &&
                !controller.signal.aborted &&
                sequence === approvalRequest
              )
                setApprovals(current.approvals ?? []);
            })
            .catch((failure: unknown) => {
              if (!controller.signal.aborted) setError(errorMessage(failure));
            });
        } else if (event.type === "error")
          setError(String(event.data.message ?? "任务未完成"));
        else if (event.type === "result")
          setProgress(String(event.data.message ?? ""));
        else if (event.type === "done") {
          finished = true;
          setRun((value) =>
            value ? { ...value, status: String(event.data.status) } : value,
          );
          void refreshMessages()
            .then(() => {
              setStreamed("");
              setCitations([]);
              setApprovals([]);
              onChanged();
            })
            .catch((failure: unknown) => setError(errorMessage(failure)));
        }
      },
      controller.signal,
    ).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    });
    return () => controller.abort();
  }, [run?.id, conversation.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages.length, streamed, approvals.length]);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (
      !input.trim() ||
      busy ||
      uploading ||
      filesPending ||
      submitting.current
    )
      return;
    submitting.current = true;
    setSending(true);
    setError("");
    setStreamed("");
    setCitations([]);
    setApprovals([]);
    const text = input.trim();
    try {
      const started = await api<Run>(`/conversations/${conversation.id}/runs`, {
        method: "POST",
        body: {
          message: text,
          attachment_ids: files
            .filter((file) => ["ready", "completed"].includes(file.status))
            .map((file) => file.id),
        },
      });
      setInput("");
      setRun(started);
      await refreshMessages();
      onChanged();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      submitting.current = false;
      setSending(false);
    }
  }
  async function upload(selected: FileList | null) {
    if (!selected?.length || uploading || busy) return;
    if (selected.length + files.length > 5) {
      setError("每个会话最多 5 个附件");
      return;
    }
    setUploading(true);
    setError("");
    try {
      for (const file of Array.from(selected)) {
        if (file.size > 20 * 1024 * 1024)
          throw new Error(`${file.name} 超过 20 MB`);
        const form = new FormData();
        form.set("conversation_id", conversation.id);
        form.set("file", file);
        await api("/files", { method: "POST", body: form });
      }
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      await refreshFiles().catch(() => undefined);
      setUploading(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }
  async function removeFile(id: string) {
    try {
      await api(`/files/${id}`, { method: "DELETE" });
      await refreshFiles();
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  async function cancel() {
    if (run)
      try {
        await api(`/runs/${run.id}/cancel`, { method: "POST" });
      } catch (failure) {
        setError(errorMessage(failure));
      }
  }

  return (
    <section className="chat-layout">
      <header className="chat-header">
        <span className="small-agent-symbol">
          <BookOpen size={21} />
        </span>
        <div>
          <h2>{agent?.name ?? "领域助手"}</h2>
          <p>{agent?.description || conversation.title}</p>
        </div>
        <span className="chat-private">
          <span className="status-dot" /> 个人会话
        </span>
      </header>
      <div className="message-scroll">
        <div className="message-column">
          {!messages.length && !streamed && (
            <div className="chat-welcome">
              <span className="welcome-symbol">
                <BookOpen size={32} strokeWidth={1.4} />
              </span>
              <h2>一起，把问题理清楚。</h2>
              <p>
                {agent?.description || "告诉我你的目标，或上传资料一起分析。"}
              </p>
              <div className="example-list">
                {agent?.examples.map((example) => (
                  <button key={example} onClick={() => setInput(example)}>
                    {example}
                    <ArrowUp size={15} />
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages
            .filter(
              (message) =>
                !(
                  busy &&
                  message.role === "assistant" &&
                  message.run_id === run?.id
                ),
            )
            .map((message) => (
              <article
                className={`message ${message.role === "user" ? "from-user" : "from-assistant"}`}
                key={message.id}
              >
                <div className="message-label">
                  {message.role === "user" ? "你" : (agent?.name ?? "领域助手")}
                </div>
                <MessageBody content={message.content} />
                <Sources citations={message.citations ?? []} />
                {message.role === "assistant" && (
                  <MessageActions message={message} />
                )}
              </article>
            ))}
          {(streamed || busy) && (
            <article className="message from-assistant">
              <div className="message-label">{agent?.name ?? "领域助手"}</div>
              {streamed ? (
                <MessageBody content={streamed} />
              ) : (
                <div className="thinking">
                  <i />
                  <i />
                  <i />
                  <span>{progress || "正在理解你的问题"}</span>
                </div>
              )}
              <Sources citations={citations} />
            </article>
          )}
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
          {run &&
            !busy &&
            ["cancelled", "outcome_unknown", "failed"].includes(run.status) && (
              <div className="run-notice">
                <StatusTag status={run.status} />
                <span>
                  {run.status === "outcome_unknown"
                    ? "操作结果需要管理员核实，请勿重复提交相同业务操作。"
                    : run.status === "cancelled"
                      ? "已停止后续处理。已经提交的业务操作仍可能生效。"
                      : "本次任务未完成，可查看提示后重新发起。"}
                </span>
              </div>
            )}
          <div ref={bottomRef} />
        </div>
      </div>
      <div className="composer-wrap">
        {error && (
          <div role="alert" className="error-banner">
            {error}
            <button
              className="icon-button"
              aria-label="关闭提示"
              onClick={() => setError("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
        {files.length > 0 && (
          <div className="attachment-list">
            {files.map((file) => (
              <div className="attachment" key={file.id}>
                <FileText size={15} />
                <span title={file.error}>{file.name}</span>
                <StatusTag status={file.status} />
                <button
                  className="icon-button"
                  aria-label={`移除 ${file.name}`}
                  disabled={busy}
                  onClick={() => void removeFile(file.id)}
                >
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
        <form className="composer" onSubmit={(event) => void send(event)}>
          <textarea
            aria-label="输入消息"
            placeholder="描述你的问题，或添加一份资料…"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            rows={2}
          />
          <div className="composer-bottom">
            <div>
              <input
                hidden
                ref={uploadRef}
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md,.markdown,.csv,.xlsx"
                onChange={(event) => void upload(event.target.files)}
              />
              <button
                type="button"
                className="attach-button"
                disabled={busy || uploading || files.length >= 5}
                onClick={() => uploadRef.current?.click()}
              >
                <Paperclip size={18} />
                {uploading ? "上传中…" : "添加资料"}
              </button>
              <span className="composer-hint">Shift + Enter 换行</span>
            </div>
            {busy ? (
              <button
                type="button"
                className="send-button stop"
                aria-label="停止任务"
                onClick={() => void cancel()}
              >
                <Square size={17} fill="currentColor" />
              </button>
            ) : (
              <button
                type="submit"
                className="send-button"
                aria-label="发送消息"
                disabled={!input.trim() || uploading || filesPending}
              >
                <ArrowUp size={21} />
              </button>
            )}
          </div>
        </form>
        <p className="composer-footnote">
          回答供工作参考，请结合引用核实重要信息。涉及业务操作时留意确认与执行结果。
        </p>
      </div>
    </section>
  );
}
