export interface User {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "member";
  group_ids: string[];
  active: boolean;
}
export interface Session {
  user: User;
  csrf_token: string;
}
export interface AgentCard {
  id: string;
  name: string;
  description: string;
  examples: string[];
}
export interface Conversation {
  id: string;
  agent_id: string;
  title: string;
  created_at: string;
  active_run_id?: string | null;
}
export interface Citation {
  id: string;
  title: string;
  locator: string;
  excerpt: string;
}
export interface Message {
  id: string;
  role: string;
  content: string;
  citations: Citation[];
  created_at: string;
  run_id: string | null;
}
export interface Approval {
  id: string;
  title: string;
  summary: string;
  parameters: Record<string, unknown>;
  expires_at: string;
}
export interface Run {
  id: string;
  status: string;
  error?: string;
  approvals?: Approval[];
}
export interface FileAsset {
  id: string;
  name: string;
  status: string;
  error?: string;
}
export interface Group {
  id: string;
  name: string;
  description: string;
}
export interface ModelConnection {
  id: string;
  name: string;
  kind: "chat" | "embedding";
  base_url: string;
  model: string;
  enabled: boolean;
  has_api_key: boolean;
}
export interface Draft {
  model_id: string;
  system_prompt: string;
  temperature: number;
  max_tokens: number;
  tool_ids: string[];
  knowledge_base_ids: string[];
  max_steps: number;
  timeout_seconds: number;
}
export interface AdminAgent extends AgentCard {
  enabled: boolean;
  allowed_group_ids: string[];
  draft: Draft;
  published_version_id: string | null;
}
export interface AgentVersion {
  id: string;
  version?: number;
  version_number?: number;
  created_at: string;
}
export interface ToolDefinition {
  id: string;
  name: string;
  title: string;
  description: string;
  transport: "http" | "mcp";
  endpoint: string;
  method: string;
  mcp_tool_name: string;
  input_schema: Record<string, unknown>;
  fixed_arguments: Record<string, unknown>;
  effect: "read" | "write";
  policy: "auto" | "confirm";
  allowed_group_ids: string[];
  timeout_seconds: number;
  idempotency_header: string;
  enabled: boolean;
  has_headers: boolean;
}
export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  embedding_model_id: string;
}
export interface DocumentRecord {
  id: string;
  name: string;
  status: string;
  error?: string;
  created_at?: string;
}
