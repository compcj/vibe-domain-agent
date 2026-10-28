import { useState, type FormEvent } from "react";
import { ArrowUpRight, LockKeyhole, BookOpen, Workflow } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import type { Session } from "../types";
import { Brand, Field } from "./Common";

export function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(
        await api<Session>("/auth/login", {
          method: "POST",
          body: { email, password },
        }),
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-layout">
      <section className="login-story">
        <Brand />
        <div className="story-content">
          <span className="eyebrow light">YOUR TEAM. YOUR EXPERTISE.</span>
          <h1>
            让专业知识，
            <br />
            成为工作的
            <br />
            <em>下一步。</em>
          </h1>
          <p>
            与领域助手一起理解资料、梳理问题，
            <br />
            把想法推进为可确认的行动。
          </p>
          <div className="story-features">
            <span>
              <BookOpen size={17} /> 有据可循的回答
            </span>
            <span>
              <Workflow size={17} /> 清晰可控的行动
            </span>
          </div>
        </div>
        <div className="story-bottom">
          <span className="status-dot" /> 组织专属工作空间
        </div>
        <div className="story-shape shape-one" />
        <div className="story-shape shape-two" />
      </section>
      <section className="login-form-area">
        <div className="login-form-wrap">
          <span className="eyebrow">WELCOME BACK</span>
          <h2>登录工作台</h2>
          <p className="muted">使用管理员为你创建的组织账号。</p>
          <form onSubmit={(event) => void submit(event)}>
            <Field label="邮箱">
              <input
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@company.com"
              />
            </Field>
            <Field label="密码">
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="输入账号密码"
              />
            </Field>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <button className="button primary login-submit" disabled={busy}>
              {busy ? "正在登录…" : "进入工作台"}
              <ArrowUpRight size={19} />
            </button>
          </form>
          <p className="login-note">
            <LockKeyhole size={15} /> 无法登录时，请联系组织管理员。
          </p>
        </div>
        <footer>Vibe Domain Agent · 为团队积累的专业而构建</footer>
      </section>
    </main>
  );
}
