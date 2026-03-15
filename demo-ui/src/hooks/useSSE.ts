import { useCallback, useRef, useState } from "react";
import { sendChatMessage } from "../api";
import type { ChatMessage, PipelineNode, SafetyDecision, ToolCallInfo } from "../types";

// Node name → display label
const NODE_LABELS: Record<string, string> = {
  consent_gate: "Consent",
  load_patient_context: "Context",
  crisis_check: "Crisis Check",
  manage_history: "History",
  pending_node: "Pending",
  onboarding_agent: "Onboarding",
  active_agent: "Active Coach",
  reengagement_agent: "Re-engage",
  dormant_node: "Dormant",
  tool_node: "Tools",
  safety_gate: "Safety",
  retry_generation: "Retry",
  fallback_response: "Fallback",
  save_patient_context: "Save",
};

/** Line-buffered SSE parser. Calls `onEvent` for each `data: ...` line. */
function createSSEParser(onEvent: (data: string) => void) {
  let buffer = "";
  return {
    feed(chunk: string) {
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          onEvent(line.slice(6));
        }
      }
    },
    flush() {
      const line = buffer.replace(/\r$/, "");
      if (line.startsWith("data: ")) {
        onEvent(line.slice(6));
      }
      buffer = "";
    },
  };
}

/** Extract text from AIMessage content that may be a string or list of blocks. */
function extractTextContent(content: unknown): string | null {
  if (typeof content === "string") return content || null;
  if (Array.isArray(content)) {
    const texts = content
      .filter(
        (b): b is { type: string; text: string } =>
          typeof b === "object" && b !== null && b.type === "text" && typeof b.text === "string",
      )
      .map((b) => b.text);
    return texts.join("") || null;
  }
  return null;
}

export interface SSEResult {
  messages: ChatMessage[];
  pipelineNodes: PipelineNode[];
  safetyDecision: { decision: SafetyDecision; confidence: number } | null;
  error: string | null;
}

export interface UseSSEReturn {
  isStreaming: boolean;
  streamingText: string;
  pipelineNodes: PipelineNode[];
  send: (
    patientId: string,
    tenantId: string,
    message: string,
  ) => Promise<SSEResult>;
}

export function useSSE(): UseSSEReturn {
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [pipelineNodes, setPipelineNodes] = useState<PipelineNode[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (
      patientId: string,
      tenantId: string,
      message: string,
    ): Promise<SSEResult> => {
      // Reset state
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      setIsStreaming(true);
      setStreamingText("");
      setPipelineNodes([]);

      const collectedMessages: ChatMessage[] = [];
      const collectedNodes: PipelineNode[] = [];
      const toolCalls: ToolCallInfo[] = [];
      let outboundMessage = "";
      let safetyResult: SSEResult["safetyDecision"] = null;
      let error: string | null = null;

      try {
        const response = await sendChatMessage(patientId, tenantId, message);
        const reader = response.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();

        const parser = createSSEParser((data) => {
          try {
            const parsed = JSON.parse(data) as Record<string, unknown>;

            // Terminal events
            if (parsed.type === "done") return;
            if (parsed.type === "error") {
              error = (parsed.message as string) ?? "Unknown error";
              return;
            }

            // Each event is { node_name: { ...state_fields } }
            for (const [nodeName, nodeData] of Object.entries(parsed)) {
              const node = nodeData as Record<string, unknown>;

              // Track pipeline progression
              const displayName = NODE_LABELS[nodeName] ?? nodeName;
              const existing = collectedNodes.find((n) => n.name === nodeName);
              if (existing) {
                existing.status = "complete";
              } else {
                // Mark any prior "running" node as complete
                for (const n of collectedNodes) {
                  if (n.status === "running") n.status = "complete";
                }
                collectedNodes.push({
                  name: nodeName,
                  displayName,
                  status: "running",
                });
              }
              setPipelineNodes([...collectedNodes]);

              // Extract outbound_message (progressive render)
              if (
                typeof node.outbound_message === "string" &&
                node.outbound_message
              ) {
                outboundMessage = node.outbound_message;
                setStreamingText(outboundMessage);
              }

              // Extract safety decision
              if (typeof node.safety_decision === "string") {
                const pending = node.pending_effects as
                  | Record<string, unknown>
                  | undefined;
                const decisions = (pending?.safety_decisions ?? []) as Array<
                  Record<string, unknown>
                >;
                const latest = decisions[decisions.length - 1];
                safetyResult = {
                  decision: node.safety_decision as SafetyDecision,
                  confidence:
                    typeof latest?.confidence === "number"
                      ? latest.confidence
                      : 0,
                };
              }

              // Extract tool calls from messages array
              if (Array.isArray(node.messages)) {
                for (const msg of node.messages) {
                  const m = msg as Record<string, unknown>;

                  // AIMessage with tool_calls
                  if (Array.isArray(m.tool_calls) && m.tool_calls.length > 0) {
                    for (const tc of m.tool_calls as Array<
                      Record<string, unknown>
                    >) {
                      if (typeof tc.name === "string") {
                        toolCalls.push({
                          id: typeof tc.id === "string" ? tc.id : undefined,
                          name: tc.name,
                          args: (tc.args as Record<string, unknown>) ?? {},
                        });
                      }
                    }
                  }

                  // ToolMessage (result) — match by tool_call_id
                  if (
                    m.type === "tool" &&
                    typeof m.content === "string"
                  ) {
                    const callId = typeof m.tool_call_id === "string" ? m.tool_call_id : undefined;
                    const matchedTc = callId
                      ? toolCalls.find((tc) => tc.id === callId)
                      : toolCalls[toolCalls.length - 1];
                    const text = extractTextContent(m.content);
                    if (text && matchedTc) {
                      collectedMessages.push({
                        id: crypto.randomUUID(),
                        role: "tool",
                        content: text,
                        toolName: matchedTc.name,
                        timestamp: new Date(),
                      });
                    }
                  }
                }
              }
            }
          } catch {
            // Skip unparseable events
          }
        });

        // Read the stream
        while (!abort.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          parser.feed(decoder.decode(value, { stream: true }));
        }
        parser.flush();

        // Mark any remaining "running" nodes as complete
        for (const n of collectedNodes) {
          if (n.status === "running") n.status = "complete";
        }
        setPipelineNodes([...collectedNodes]);

        // Release the reader if aborted to avoid resource leak
        if (abort.signal.aborted) {
          await reader.cancel();
        }
      } catch (err) {
        error = err instanceof Error ? err.message : String(err);
      } finally {
        if (!abort.signal.aborted) {
          setIsStreaming(false);
        }
      }

      // Build final assistant message
      if (outboundMessage) {
        collectedMessages.push({
          id: crypto.randomUUID(),
          role: "assistant",
          content: outboundMessage,
          timestamp: new Date(),
        });
      } else if (error) {
        collectedMessages.push({
          id: crypto.randomUUID(),
          role: "error",
          content: error,
          timestamp: new Date(),
        });
      }

      return {
        messages: collectedMessages,
        pipelineNodes: collectedNodes,
        safetyDecision: safetyResult,
        error,
      };
    },
    [],
  );

  return { isStreaming, streamingText, pipelineNodes, send };
}
