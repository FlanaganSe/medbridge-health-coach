import { Bot, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSSE } from "../hooks/useSSE";
import type { ChatMessage, Phase, SafetyDecision } from "../types";
import { PhaseBadge } from "./ui/Badge";
import { ChatMessageBubble } from "./ChatMessage";
import { PipelineTrace } from "./PipelineTrace";
import { SafetyToast } from "./SafetyToast";

interface ChatPanelProps {
  patientId: string;
  tenantId: string;
  phase: Phase;
  onStreamComplete: () => void;
}

export function ChatPanel({
  patientId,
  tenantId,
  phase,
  onStreamComplete,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { isStreaming, streamingText, pipelineNodes, send } = useSSE();

  // Safety toast state
  const [safetyToast, setSafetyToast] = useState<{
    decision: SafetyDecision;
    confidence: number;
  } | null>(null);
  const dismissToast = useCallback(() => setSafetyToast(null), []);

  // Scroll to bottom on new messages or streaming text
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText]);

  // Reset on patient change
  useEffect(() => {
    setMessages([]);
  }, [patientId]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    setInput("");

    // Add user message immediately
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMsg]);

    // Stream the response
    const result = await send(patientId, tenantId, text);

    // Add all collected messages (tool calls + assistant response)
    setMessages((prev) => [...prev, ...result.messages]);

    // Show safety toast if relevant
    if (
      result.safetyDecision &&
      result.safetyDecision.decision !== "safe"
    ) {
      setSafetyToast(result.safetyDecision);
    }

    onStreamComplete();
  }, [input, isStreaming, patientId, tenantId, send, onStreamComplete]);

  return (
    <div className="flex flex-1 flex-col border-r border-border bg-white">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-blue-badge-bg">
            <Bot size={18} className="text-blue-badge-text" />
          </div>
          <div>
            <div className="font-heading text-[15px] font-semibold text-text-primary">
              Health Ally
            </div>
            <div className="text-xs text-text-secondary">
              AI-powered coaching assistant
            </div>
          </div>
        </div>
        <PhaseBadge phase={phase} />
      </div>

      {/* Pipeline trace */}
      <PipelineTrace nodes={pipelineNodes} isStreaming={isStreaming} />

      {/* Messages */}
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto p-6">
        {messages.length === 0 && !isStreaming && (
          <div className="flex flex-1 items-center justify-center text-sm text-text-muted">
            Send a message to start the conversation.
          </div>
        )}

        {messages.map((msg) => (
          <ChatMessageBubble key={msg.id} {...msg} />
        ))}

        {/* Streaming indicator */}
        {isStreaming && streamingText && (
          <div className="flex w-full gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-badge-bg">
              <Bot size={18} className="text-blue-badge-text" />
            </div>
            <div className="max-w-[520px] rounded-bl-xl rounded-br-xl rounded-tr-xl bg-bg-subtle px-4 py-3">
              <p className="text-sm leading-relaxed text-text-primary whitespace-pre-wrap">
                {streamingText}
              </p>
            </div>
          </div>
        )}

        {isStreaming && !streamingText && (
          <div className="flex items-center gap-2 text-sm text-text-muted">
            <span className="inline-flex gap-0.5">
              <span className="animate-bounce" style={{ animationDelay: "0ms" }}>.</span>
              <span className="animate-bounce" style={{ animationDelay: "150ms" }}>.</span>
              <span className="animate-bounce" style={{ animationDelay: "300ms" }}>.</span>
            </span>
            <span>Thinking</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input bar */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleSend();
        }}
        className="flex shrink-0 items-center gap-3 border-t border-border bg-white px-6 py-4"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          maxLength={4000}
          disabled={isStreaming}
          className="h-11 flex-1 rounded-lg border border-border bg-bg-subtle px-4 text-sm text-text-primary placeholder:text-text-muted outline-none focus:border-blue-badge-text"
        />
        <button
          type="submit"
          disabled={isStreaming || !input.trim()}
          aria-label="Send message"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-brand-red text-white transition-opacity disabled:opacity-40"
        >
          <Send size={18} />
        </button>
      </form>

      {/* Safety toast */}
      <SafetyToast toast={safetyToast} onDismiss={dismissToast} />
    </div>
  );
}
