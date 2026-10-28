import { useEffect, useRef, useState } from "react";
import { BookOpen } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import type { Citation } from "../types";

function Source({ citation, index }: { citation: Citation; index: number }) {
  const [current, setCurrent] = useState<Citation | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function load(open: boolean) {
    request.current?.abort();
    setCurrent(null);
    setError("");
    setLoading(open);
    if (!open) return;
    const controller = new AbortController();
    request.current = controller;
    try {
      const result = await api<Citation>(
        `/citations/${encodeURIComponent(citation.id)}`,
        {
          signal: controller.signal,
        },
      );
      if (!controller.signal.aborted) setCurrent(result);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  return (
    <details onToggle={(event) => void load(event.currentTarget.open)}>
      <summary>
        <BookOpen size={13} />
        <span>
          {index + 1}. {citation.title}
        </span>
      </summary>
      {loading && <p className="muted">正在读取来源…</p>}
      {current && (
        <>
          <small>{current.locator}</small>
          <p>{current.excerpt}</p>
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </details>
  );
}

export function Sources({ citations }: { citations: Citation[] }) {
  const [expanded, setExpanded] = useState(false);
  return citations.length ? (
    <details
      className="source-group"
      onToggle={(event) => {
        if (event.target === event.currentTarget)
          setExpanded(event.currentTarget.open);
      }}
    >
      <summary>参考资料（{citations.length} 个片段）</summary>
      {expanded && (
        <div className="citations">
          {citations.map((citation, index) => (
            <Source citation={citation} index={index} key={citation.id} />
          ))}
        </div>
      )}
    </details>
  ) : null;
}
