import { useState } from "react";
import { ArrowUpRight, BookOpen, Search, Sparkles } from "lucide-react";
import type { AgentCard, User } from "../types";
import { Empty } from "./Common";

export function Catalog({
  agents,
  user,
  busy,
  onSelect,
}: {
  agents: AgentCard[];
  user: User;
  busy: boolean;
  onSelect: (agent: AgentCard) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = agents.filter((agent) =>
    `${agent.name} ${agent.description}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <div className="catalog page-enter">
      <div className="catalog-heading">
        <span className="eyebrow">YOUR KNOWLEDGE, IN ACTION</span>
        <h1>
          你好，{user.display_name}。<br />
          <span>今天，推进哪一件事？</span>
        </h1>
        <p>选择一位领域助手，从一个问题或一份资料开始。</p>
        <span className="heading-orbit">
          <Sparkles size={40} strokeWidth={1.2} />
        </span>
      </div>
      <div className="section-heading">
        <div>
          <h2>
            你的领域助手 <span className="count">{agents.length}</span>
          </h2>
          <p>组织为你准备的专业能力</p>
        </div>
        <label className="search-box">
          <Search size={17} />
          <input
            aria-label="搜索助手"
            placeholder="搜索助手"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </div>
      {filtered.length ? (
        <div className="agent-grid">
          {filtered.map((agent, index) => (
            <button
              key={agent.id}
              className={`agent-card accent-${index % 4}`}
              disabled={busy}
              onClick={() => onSelect(agent)}
            >
              <div className="agent-card-top">
                <span className="agent-symbol">
                  <BookOpen size={25} strokeWidth={1.5} />
                </span>
                <span className="agent-arrow">
                  <ArrowUpRight size={21} />
                </span>
              </div>
              <h3>{agent.name}</h3>
              <p>{agent.description || "从你的问题出发，获取专业的帮助。"}</p>
              <div className="agent-card-bottom">
                <span>
                  <i className="status-dot" /> 已为你开放
                </span>
                <span>开始对话</span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <Empty
          title={query ? "没有找到相关助手" : "你的助手即将就绪"}
          description={
            query
              ? "试试其他关键词。"
              : "管理员发布助手并为你授权后，将显示在这里。"
          }
        />
      )}
      <div className="catalog-note">
        <span className="note-number">01 /</span>
        <div>
          <strong>让问题更具体，让答案更有用</strong>
          <p>
            提供目标、背景与资料，助手会结合可用知识给出回答。涉及业务操作时，请留意执行结果。
          </p>
        </div>
      </div>
    </div>
  );
}
