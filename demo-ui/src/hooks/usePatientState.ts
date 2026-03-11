import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchAlerts,
  fetchGoals,
  fetchPhase,
  fetchSafetyDecisions,
  fetchScheduledJobs,
} from "../api";
import type { PatientState } from "../types";

const FALLBACK_POLL_MS = 10_000;

const EMPTY_STATE: PatientState = {
  phase: "pending",
  goals: [],
  alerts: [],
  safetyDecisions: [],
  scheduledJobs: [],
};

type LoadState = "loading" | "loaded" | "error";

export interface UsePatientStateReturn {
  state: PatientState;
  loadState: LoadState;
  lastUpdated: Date | null;
  refresh: () => void;
}

export function usePatientState(
  patientId: string,
  tenantId: string,
): UsePatientStateReturn {
  const [state, setState] = useState<PatientState>(EMPTY_STATE);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const tokenRef = useRef<object | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = useCallback(async () => {
    // Capture the current token so we can detect if the patient changed mid-fetch
    const token = tokenRef.current;
    try {
      const [phase, goals, alerts, safetyDecisions, scheduledJobs] =
        await Promise.all([
          fetchPhase(patientId, tenantId).catch(() => "pending" as const),
          fetchGoals(patientId, tenantId).catch(() => []),
          fetchAlerts(patientId, tenantId).catch(() => []),
          fetchSafetyDecisions(patientId, tenantId).catch(() => []),
          fetchScheduledJobs(patientId).catch(() => []),
        ]);

      if (tokenRef.current !== token) return;

      setState({ phase, goals, alerts, safetyDecisions, scheduledJobs });
      setLoadState("loaded");
      setLastUpdated(new Date());
    } catch {
      if (tokenRef.current === token) setLoadState("error");
    }
  }, [patientId, tenantId]);

  // Expose refresh for event-driven updates (called after SSE completes)
  const refresh = useCallback(() => {
    void fetchAll();
  }, [fetchAll]);

  // Initial fetch + fallback polling
  useEffect(() => {
    const token = {};
    tokenRef.current = token;
    setLoadState("loading");
    setState(EMPTY_STATE);
    void fetchAll();

    intervalRef.current = setInterval(fetchAll, FALLBACK_POLL_MS);

    return () => {
      tokenRef.current = null;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchAll]);

  return { state, loadState, lastUpdated, refresh };
}
