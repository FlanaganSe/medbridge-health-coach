// --- Phases ---

export type Phase =
  | "pending"
  | "onboarding"
  | "active"
  | "re_engaging"
  | "dormant";

// --- API Response Types ---

export interface GoalItem {
  id: string;
  goal_text: string;
  confirmed_at: string | null;
  created_at: string;
}

export interface AlertItem {
  id: string;
  reason: string;
  priority: "routine" | "urgent";
  acknowledged_at: string | null;
  created_at: string;
}

export type SafetyDecision =
  | "safe"
  | "block"
  | "clinical_boundary"
  | "crisis";

export interface SafetyDecisionItem {
  id: string;
  decision: SafetyDecision;
  source: string;
  confidence: number;
  created_at: string;
}

export interface ScheduledJobItem {
  id: string;
  job_type: string;
  status: "pending" | "processing" | "completed" | "cancelled" | "dead";
  scheduled_at: string;
  attempts: number;
  max_attempts: number;
  created_at: string;
}

export interface AuditEventItem {
  id: string;
  event_type: string;
  outcome: string;
  created_at: string;
}

export interface ConversationMessage {
  role: "human" | "ai" | "tool";
  content: string;
  tool_name?: string;
  message_id: string;
}

// --- API Response Wrappers ---

export interface DemoPatient {
  patient_id: string;
  external_patient_id: string;
  display_name: string | null;
  phase: Phase;
  created_at: string;
}

export interface SeedPatientResponse {
  patient_id: string;
  external_patient_id: string;
  display_name: string | null;
  phase: Phase;
}

export interface RunCheckinResponse {
  patient_id: string;
  phase: Phase;
  status: string;
}

export interface DeletePatientResponse {
  patient_id: string;
  deleted: boolean;
}

export interface ResetPatientResponse {
  patient_id: string;
  phase: Phase;
  deleted_goals: number;
  deleted_jobs: number;
  deleted_outbox: number;
}

// --- Chat Messages ---

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool" | "error";
  content: string;
  timestamp: Date;
  toolName?: string;
}

// --- SSE Types ---

export interface ToolCallInfo {
  id?: string;
  name: string;
  args: Record<string, unknown>;
}

export interface PipelineNode {
  name: string;
  displayName: string;
  status: "pending" | "running" | "complete";
}

// --- Patient State (sidebar) ---

export interface PatientState {
  phase: Phase;
  goals: GoalItem[];
  alerts: AlertItem[];
  safetyDecisions: SafetyDecisionItem[];
  scheduledJobs: ScheduledJobItem[];
  auditEvents: AuditEventItem[];
  conversationHistory: ConversationMessage[];
}
