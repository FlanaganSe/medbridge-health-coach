import type {
  AlertItem,
  AuditEventItem,
  ConversationMessage,
  DeletePatientResponse,
  DemoPatient,
  GoalItem,
  Phase,
  ResetPatientResponse,
  RunCheckinResponse,
  SafetyDecisionItem,
  ScheduledJobItem,
  SeedPatientResponse,
  SetPhaseResponse,
} from "./types";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text();
    let message = text;
    try {
      const json = JSON.parse(text) as { detail?: string };
      if (json.detail) message = json.detail;
    } catch {
      // not JSON — use raw text
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

function authHeaders(patientId: string, tenantId: string): HeadersInit {
  return {
    "X-Patient-ID": patientId,
    "X-Tenant-ID": tenantId,
  };
}

// --- Demo Endpoints ---

export function seedPatient(
  tenantId: string,
  externalPatientId: string,
  displayName?: string,
): Promise<SeedPatientResponse> {
  return request("/v1/demo/seed-patient", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tenant_id: tenantId,
      external_patient_id: externalPatientId,
      display_name: displayName ?? null,
    }),
  });
}

export async function listPatients(
  tenantId: string,
): Promise<DemoPatient[]> {
  const r = await request<{ patients: DemoPatient[] }>(
    `/v1/demo/patients?tenant_id=${encodeURIComponent(tenantId)}`,
  );
  return r.patients;
}

export function deletePatient(
  patientId: string,
): Promise<DeletePatientResponse> {
  return request(`/v1/demo/patients/${patientId}`, {
    method: "DELETE",
  });
}

export function runCheckin(
  patientId: string,
): Promise<RunCheckinResponse> {
  return request(`/v1/demo/run-checkin/${patientId}`, {
    method: "POST",
  });
}

export function resetPatient(
  patientId: string,
): Promise<ResetPatientResponse> {
  return request(`/v1/demo/reset-patient/${patientId}`, {
    method: "POST",
  });
}

export function setPhase(
  patientId: string,
  phase: Phase,
): Promise<SetPhaseResponse> {
  return request(`/v1/demo/patients/${patientId}/phase`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phase }),
  });
}

export async function fetchScheduledJobs(
  patientId: string,
): Promise<ScheduledJobItem[]> {
  const r = await request<{ jobs: ScheduledJobItem[] }>(
    `/v1/demo/scheduled-jobs/${patientId}`,
  );
  return r.jobs;
}

export async function fetchAuditEvents(
  patientId: string,
): Promise<AuditEventItem[]> {
  const r = await request<{ events: AuditEventItem[] }>(
    `/v1/demo/audit-events/${patientId}`,
  );
  return r.events;
}

export async function fetchConversationHistory(
  patientId: string,
): Promise<ConversationMessage[]> {
  const r = await request<{ messages: ConversationMessage[] }>(
    `/v1/demo/conversation/${patientId}`,
  );
  return r.messages;
}

// --- State Endpoints ---

export async function fetchPhase(
  patientId: string,
  tenantId: string,
): Promise<Phase> {
  const r = await request<{ patient_id: string; phase: Phase }>(
    `/v1/patients/${patientId}/phase`,
    { headers: authHeaders(patientId, tenantId) },
  );
  return r.phase;
}

export async function fetchGoals(
  patientId: string,
  tenantId: string,
): Promise<GoalItem[]> {
  const r = await request<{ patient_id: string; goals: GoalItem[] }>(
    `/v1/patients/${patientId}/goals`,
    { headers: authHeaders(patientId, tenantId) },
  );
  return r.goals;
}

export async function fetchAlerts(
  patientId: string,
  tenantId: string,
): Promise<AlertItem[]> {
  const r = await request<{ patient_id: string; alerts: AlertItem[] }>(
    `/v1/patients/${patientId}/alerts`,
    { headers: authHeaders(patientId, tenantId) },
  );
  return r.alerts;
}

export async function fetchSafetyDecisions(
  patientId: string,
  tenantId: string,
): Promise<SafetyDecisionItem[]> {
  const r = await request<{
    patient_id: string;
    decisions: SafetyDecisionItem[];
  }>(`/v1/patients/${patientId}/safety-decisions`, {
    headers: authHeaders(patientId, tenantId),
  });
  return r.decisions;
}

// --- Chat (returns raw Response for SSE streaming) ---

export async function sendChatMessage(
  patientId: string,
  tenantId: string,
  message: string,
): Promise<Response> {
  const res = await fetch("/v1/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(patientId, tenantId),
    },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text;
    try {
      const json = JSON.parse(text) as { detail?: string };
      if (json.detail) message = json.detail;
    } catch {
      // not JSON — use raw text
    }
    throw new ApiError(res.status, message);
  }
  return res;
}
