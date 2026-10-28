import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  LayoutGrid,
  LogOut,
  Menu,
  MessageSquare,
  Plus,
  Settings2,
  Trash2,
  KeyRound,
  X,
} from "lucide-react";
import { api, errorMessage, setCsrfToken } from "./lib/api";
import type { AgentCard, Conversation, Session, User } from "./types";
import { Brand, Field, Modal, Spinner } from "./components/Common";
import { Login } from "./components/Login";
import { Catalog } from "./components/Catalog";
import { Chat } from "./components/Chat";
import { Admin } from "./components/admin/Admin";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  function login(session: Session) {
    setCsrfToken(session.csrf_token);
    setUser(session.user);
  }
  function signedOut() {
    setUser(null);
    setCsrfToken("");
  }
  useEffect(() => {
    void api<Session>("/auth/me")
      .then(login)
      .catch(() => undefined)
      .finally(() => setLoading(false));
    window.addEventListener("session-expired", signedOut);
    return () => window.removeEventListener("session-expired", signedOut);
  }, []);
  if (loading)
    return (
      <div className="app-loading">
        <Brand />
        <Spinner label="正在连接工作空间" />
      </div>
    );
  if (!user) return <Login onLogin={login} />;
  return <Workspace key={user.id} user={user} onSignedOut={signedOut} />;
}

function Workspace({
  user,
  onSignedOut,
}: {
  user: User;
  onSignedOut: () => void;
}) {
  const [agents, setAgents] = useState<AgentCard[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [view, setView] = useState<"catalog" | "chat" | "admin">("catalog");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");

  const refresh = useCallback(async () => {
    const [available, history] = await Promise.all([
      api<AgentCard[]>("/agents"),
      api<Conversation[]>("/conversations"),
    ]);
    setAgents(available);
    setConversations(history);
    return history;
  }, []);
  useEffect(() => {
    if (!user) return;
    void refresh()
      .then((history) => {
        const id = new URL(window.location.href).searchParams.get(
          "conversation",
        );
        const found = history.find((conversation) => conversation.id === id);
        if (found) {
          setSelected(found);
          setView("chat");
        }
      })
      .catch((failure: unknown) => setError(errorMessage(failure)));
  }, [user?.id, refresh]);
  function select(conversation: Conversation) {
    setSelected(conversation);
    setView("chat");
    setMenuOpen(false);
    const url = new URL(window.location.href);
    url.searchParams.set("conversation", conversation.id);
    history.replaceState(null, "", url);
  }
  function catalog() {
    setView("catalog");
    setMenuOpen(false);
    const url = new URL(window.location.href);
    url.searchParams.delete("conversation");
    history.replaceState(null, "", url);
  }
  async function start(agent: AgentCard) {
    setBusy(true);
    try {
      const conversation = await api<Conversation>("/conversations", {
        method: "POST",
        body: { agent_id: agent.id, title: agent.name },
      });
      await refresh();
      select(conversation);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function remove(conversation: Conversation) {
    if (!window.confirm(`删除“${conversation.title}”及其附件？`)) return;
    try {
      await api(`/conversations/${conversation.id}`, { method: "DELETE" });
      if (selected?.id === conversation.id) {
        setSelected(null);
        catalog();
      }
      await refresh();
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      onSignedOut();
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  async function changePassword() {
    setBusy(true);
    try {
      await api("/auth/password", {
        method: "POST",
        body: { current_password: currentPassword, new_password: newPassword },
      });
      setPasswordOpen(false);
      setCurrentPassword("");
      setNewPassword("");
      onSignedOut();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="app-shell">
      <button
        className="mobile-menu icon-button"
        aria-label="打开导航"
        onClick={() => setMenuOpen(true)}
      >
        <Menu size={23} />
      </button>
      {menuOpen && (
        <button
          aria-label="关闭导航"
          className="sidebar-scrim"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <Brand />
        <button className="new-chat" onClick={catalog}>
          <Plus size={18} />
          开启新对话<span>↗</span>
        </button>
        <nav>
          <button
            className={view === "catalog" ? "selected" : ""}
            onClick={catalog}
          >
            <LayoutGrid size={18} />
            助手工作台
          </button>
          {user.role === "admin" && (
            <button
              className={view === "admin" ? "selected" : ""}
              onClick={() => {
                setView("admin");
                setMenuOpen(false);
              }}
            >
              <Settings2 size={18} />
              组织管理<span className="nav-admin">ADMIN</span>
            </button>
          )}
        </nav>
        <div className="history-label">
          最近的对话 <span>{conversations.length}</span>
        </div>
        <div className="history-list">
          {conversations.length ? (
            conversations.map((conversation) => (
              <div
                className={`history-item ${view === "chat" && selected?.id === conversation.id ? "selected" : ""}`}
                key={conversation.id}
              >
                <button onClick={() => select(conversation)}>
                  <MessageSquare size={15} />
                  <span>{conversation.title}</span>
                </button>
                <button
                  className="history-delete"
                  aria-label={`删除 ${conversation.title}`}
                  onClick={() => void remove(conversation)}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          ) : (
            <p className="history-empty">你的对话会保存在这里</p>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="workspace-badge">
            <span className="status-dot" /> 组织工作空间
          </div>
          <div className="user-panel">
            <span className="avatar">{user.display_name.slice(0, 1)}</span>
            <div>
              <strong>{user.display_name}</strong>
              <small>{user.role === "admin" ? "管理员" : "组织成员"}</small>
            </div>
            <button
              className="icon-button"
              aria-label="修改密码"
              onClick={() => setPasswordOpen(true)}
            >
              <KeyRound size={16} />
            </button>
            <button
              className="icon-button"
              aria-label="退出登录"
              onClick={() => void logout()}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <main className={`main-content ${view === "chat" ? "chat-main" : ""}`}>
        {error && (
          <div className="global-error error-banner" role="alert">
            {error}
            <button
              aria-label="关闭错误"
              className="icon-button"
              onClick={() => setError("")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {view === "catalog" && (
          <Catalog
            agents={agents}
            user={user}
            busy={busy}
            onSelect={(agent) => void start(agent)}
          />
        )}
        {view === "chat" && selected && (
          <Chat
            key={selected.id}
            conversation={selected}
            agent={agents.find((agent) => agent.id === selected.agent_id)}
            onChanged={() =>
              void refresh().catch((failure: unknown) =>
                setError(errorMessage(failure)),
              )
            }
          />
        )}
        {view === "admin" && user.role === "admin" && (
          <>
            <button className="back-workspace" onClick={catalog}>
              <ArrowLeft size={15} />
              返回工作台
            </button>
            <Admin
              onChanged={() =>
                void refresh().catch((failure: unknown) =>
                  setError(errorMessage(failure)),
                )
              }
            />
          </>
        )}
      </main>
      {passwordOpen && (
        <Modal title="修改密码" onClose={() => setPasswordOpen(false)}>
          <form
            className="modal-form"
            onSubmit={(event) => {
              event.preventDefault();
              void changePassword();
            }}
          >
            <Field label="当前密码">
              <input
                type="password"
                autoComplete="current-password"
                required
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
              />
            </Field>
            <Field label="新密码" hint="至少 12 个字符。修改后需要重新登录。">
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
              />
            </Field>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <div className="form-actions">
              <button className="button primary" disabled={busy}>
                保存新密码
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
