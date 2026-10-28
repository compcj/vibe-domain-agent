import { useState } from "react";
import { KeyRound, Pencil, Plus, Trash2, Upload, Users } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import type { Group, User } from "../../types";
import { Empty, Field, Modal, Spinner } from "../Common";
import { FormActions, GroupPicker, Toolbar, useResource } from "./shared";

export function UsersPanel() {
  const users = useResource<User>("/admin/users");
  const groups = useResource<Group>("/admin/groups");
  const [editing, setEditing] = useState<User | null | undefined>();
  const [groupEditing, setGroupEditing] = useState<Group | null | undefined>();
  const [resetting, setResetting] = useState<User | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [importing, setImporting] = useState(false);
  const [importText, setImportText] = useState("");
  const [busy, setBusy] = useState(false);
  async function deleteGroup(group: Group) {
    if (!window.confirm(`删除用户组“${group.name}”？关联授权将移除。`)) return;
    try {
      await api(`/admin/groups/${group.id}`, { method: "DELETE" });
      await Promise.all([groups.reload(), users.reload()]);
    } catch (failure) {
      users.setError(errorMessage(failure));
    }
  }
  async function reset() {
    if (!resetting) return;
    setBusy(true);
    try {
      await api(`/admin/users/${resetting.id}/reset-password`, {
        method: "POST",
        body: { password: resetPassword },
      });
      setResetting(null);
      setResetPassword("");
    } catch (failure) {
      users.setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function importUsers() {
    setBusy(true);
    try {
      const entries: unknown = JSON.parse(importText);
      if (!Array.isArray(entries)) throw new Error("导入内容必须是用户数组");
      await api("/admin/users/import", {
        method: "POST",
        body: { users: entries },
      });
      setImporting(false);
      setImportText("");
      await users.reload();
    } catch (failure) {
      users.setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Toolbar
        title="成员与用户组"
        description="管理组织账号，以用户组分配助手和工具权限。"
        action="添加成员"
        onAdd={() => setEditing(null)}
        onRefresh={() => void Promise.all([users.reload(), groups.reload()])}
      />
      {(users.error || groups.error) && (
        <p className="error-banner" role="alert">
          {users.error || groups.error}
        </p>
      )}
      <div className="group-strip">
        <strong>
          <Users size={16} />
          用户组
        </strong>
        {groups.data.map((group) => (
          <div className="group-chip" key={group.id}>
            <button onClick={() => setGroupEditing(group)}>{group.name}</button>
            <button
              aria-label={`删除用户组 ${group.name}`}
              onClick={() => void deleteGroup(group)}
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        <button className="button text" onClick={() => setGroupEditing(null)}>
          <Plus size={14} />
          新增用户组
        </button>
      </div>
      <div className="list-tools">
        <span>{users.data.length} 位成员</span>
        <button
          className="button secondary"
          onClick={() => {
            users.setError("");
            setImporting(true);
          }}
        >
          <Upload size={15} />
          批量导入
        </button>
      </div>
      {users.loading ? (
        <Spinner />
      ) : users.data.length ? (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>成员</th>
                <th>角色</th>
                <th>用户组</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((user) => (
                <tr key={user.id}>
                  <td>
                    <strong>{user.display_name}</strong>
                    <small>{user.email}</small>
                  </td>
                  <td>{user.role === "admin" ? "管理员" : "成员"}</td>
                  <td>
                    {user.group_ids
                      .map(
                        (id) =>
                          groups.data.find((group) => group.id === id)?.name ??
                          "",
                      )
                      .filter(Boolean)
                      .join("、") || "未分配"}
                  </td>
                  <td>
                    <span className={`tag ${user.active ? "" : "neutral"}`}>
                      {user.active ? "启用" : "停用"}
                    </span>
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      aria-label={`编辑成员 ${user.display_name}`}
                      onClick={() => setEditing(user)}
                    >
                      <Pencil size={16} />
                    </button>
                    <button
                      className="icon-button"
                      aria-label={`重置密码 ${user.display_name}`}
                      onClick={() => {
                        setResetting(user);
                        setResetPassword("");
                        users.setError("");
                      }}
                    >
                      <KeyRound size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title="添加组织成员"
          description="创建账号并分配用户组后，成员即可访问被授权的助手。"
        />
      )}
      {editing !== undefined && (
        <UserEditor
          user={editing}
          groups={groups.data}
          onClose={() => setEditing(undefined)}
          onSaved={async () => {
            setEditing(undefined);
            await users.reload();
          }}
        />
      )}
      {groupEditing !== undefined && (
        <GroupEditor
          group={groupEditing}
          onClose={() => setGroupEditing(undefined)}
          onSaved={async () => {
            setGroupEditing(undefined);
            await groups.reload();
          }}
        />
      )}
      {resetting && (
        <Modal
          title={`重置 ${resetting.display_name} 的密码`}
          onClose={() => setResetting(null)}
        >
          <form
            className="modal-form"
            onSubmit={(event) => {
              event.preventDefault();
              void reset();
            }}
          >
            <Field
              label="新密码"
              hint="至少 12 个字符；该成员已有登录会话将失效。"
            >
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                value={resetPassword}
                onChange={(event) => setResetPassword(event.target.value)}
              />
            </Field>
            {users.error && (
              <p className="error" role="alert">
                {users.error}
              </p>
            )}
            <FormActions busy={busy} onClose={() => setResetting(null)}>
              重置密码
            </FormActions>
          </form>
        </Modal>
      )}
      {importing && (
        <Modal title="批量导入成员" onClose={() => setImporting(false)} wide>
          <form
            className="modal-form"
            onSubmit={(event) => {
              event.preventDefault();
              void importUsers();
            }}
          >
            <p className="muted">
              导入 JSON 数组，每项包含 email、display_name、password、role 和
              group_ids。任一条无效则整批不写入。
            </p>
            <Field label="读取 JSON 文件">
              <input
                type="file"
                accept=".json,application/json"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void file.text().then(setImportText);
                }}
              />
            </Field>
            <Field label="用户数据">
              <textarea
                rows={12}
                className="code-input"
                required
                value={importText}
                onChange={(event) => setImportText(event.target.value)}
                placeholder='[{"email":"member@example.com","display_name":"成员姓名","password":"","role":"member","group_ids":[]}]'
              />
            </Field>
            {users.error && (
              <p className="error" role="alert">
                {users.error}
              </p>
            )}
            <FormActions busy={busy} onClose={() => setImporting(false)}>
              校验并导入
            </FormActions>
          </form>
        </Modal>
      )}
    </>
  );
}

function UserEditor({
  user,
  groups,
  onClose,
  onSaved,
}: {
  user: User | null;
  groups: Group[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [email, setEmail] = useState(user?.email ?? "");
  const [name, setName] = useState(user?.display_name ?? "");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(user?.role ?? "member");
  const [active, setActive] = useState(user?.active ?? true);
  const [groupIds, setGroupIds] = useState(user?.group_ids ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    try {
      await api(user ? `/admin/users/${user.id}` : "/admin/users", {
        method: user ? "PATCH" : "POST",
        body: {
          email,
          display_name: name,
          role,
          active,
          group_ids: groupIds,
          ...(!user ? { password } : {}),
        },
      });
      await onSaved();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={user ? "编辑成员" : "添加成员"} onClose={onClose}>
      <form
        className="modal-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Field label="姓名">
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="邮箱">
          <input
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        {!user && (
          <Field
            label="初始密码"
            hint="至少 12 个字符，通过组织认可的方式告知成员。"
          >
            <input
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
        )}
        <Field label="角色">
          <select
            value={role}
            onChange={(event) =>
              setRole(event.target.value as "member" | "admin")
            }
          >
            <option value="member">成员</option>
            <option value="admin">管理员</option>
          </select>
        </Field>
        <GroupPicker
          groups={groups}
          value={groupIds}
          onChange={setGroupIds}
          label="所属用户组"
        />
        <label className="check-inline">
          <input
            type="checkbox"
            checked={active}
            onChange={(event) => setActive(event.target.checked)}
          />
          账号启用
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <FormActions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}

function GroupEditor({
  group,
  onClose,
  onSaved,
}: {
  group: Group | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    try {
      await api(group ? `/admin/groups/${group.id}` : "/admin/groups", {
        method: group ? "PATCH" : "POST",
        body: { name, description },
      });
      await onSaved();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={group ? "编辑用户组" : "新增用户组"} onClose={onClose}>
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
        <Field label="说明">
          <textarea
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <FormActions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}
