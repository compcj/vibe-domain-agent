import { useState } from "react";
import { Bot, Boxes, BookOpen, Wrench, Users, Activity } from "lucide-react";
import { AgentsPanel } from "./AgentsPanel";
import { ModelsPanel } from "./ModelsPanel";
import { KnowledgePanel } from "./KnowledgePanel";
import { ToolsPanel } from "./ToolsPanel";
import { UsersPanel } from "./UsersPanel";
import { OperationsPanel } from "./OperationsPanel";

const panels = [
  { id: "agents", label: "Agent", icon: Bot, component: AgentsPanel },
  { id: "models", label: "模型连接", icon: Boxes, component: ModelsPanel },
  {
    id: "knowledge",
    label: "知识库",
    icon: BookOpen,
    component: KnowledgePanel,
  },
  { id: "tools", label: "业务工具", icon: Wrench, component: ToolsPanel },
  { id: "users", label: "成员与用户组", icon: Users, component: UsersPanel },
  {
    id: "operations",
    label: "运行与审计",
    icon: Activity,
    component: OperationsPanel,
  },
];
export function Admin({ onChanged }: { onChanged: () => void }) {
  const [selected, setSelected] = useState("agents");
  const Panel = panels.find((item) => item.id === selected)!.component;
  return (
    <div className="admin-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE SETTINGS</span>
          <h1>管理工作台</h1>
          <p>配置专业能力，让成员专注业务。</p>
        </div>
        <span className="pill">管理员</span>
      </header>
      <nav className="admin-tabs" aria-label="管理模块">
        {panels.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={selected === id ? "active" : ""}
            onClick={() => setSelected(id)}
          >
            <Icon size={17} />
            {label}
          </button>
        ))}
      </nav>
      <Panel onChanged={onChanged} />
    </div>
  );
}
