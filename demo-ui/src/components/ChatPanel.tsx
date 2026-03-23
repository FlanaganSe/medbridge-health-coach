import { Bot, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSSE } from "../hooks/useSSE";
import type { ChatMessage, Phase, SafetyDecision } from "../types";
import { PhaseBadge } from "./ui/Badge";
import { ChatMessageBubble } from "./ChatMessage";
import { PipelineStepper } from "./PipelineStepper";
import { SafetyToast } from "./SafetyToast";

function getSuggestions(phase: Phase): string[] {
  switch (phase) {
    case "active":
      return [
        "How am I doing with my exercises?",
        "I need help staying on track",
      ];
    case "re_engaging":
    case "dormant":
      return [
        "I'm back and ready to try again",
        "Can we pick up where we left off?",
      ];
    default:
      return [
        "Hi! I'd like to get started",
        "What can you help me with?",
      ];
  }
}

function SuggestionChips({
  phase,
  onSelect,
}: {
  phase: Phase;
  onSelect: (text: string) => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3">
      <p className="text-sm text-text-muted">
        Send a message to start the conversation.
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {getSuggestions(phase).map((text) => (
          <button
            key={text}
            type="button"
            onClick={() => onSelect(text)}
            className="rounded-full border border-border-primary px-4 py-2 text-sm text-text-secondary transition-colors hover:border-teal/30 hover:bg-teal-light hover:text-teal"
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

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

  const handleSend = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || isStreaming) return;

    if (!overrideText) setInput("");

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

    // Show safety toast only for hard-blocking decisions.
    // Clinical boundary is advisory — logged but doesn't replace the message.
    if (
      result.safetyDecision &&
      result.safetyDecision.decision !== "safe" &&
      result.safetyDecision.decision !== "clinical_boundary"
    ) {
      setSafetyToast(result.safetyDecision);
    }

    onStreamComplete();
  }, [input, isStreaming, patientId, tenantId, send, onStreamComplete]);

  const handleChipSelect = useCallback(
    (text: string) => { void handleSend(text); },
    [handleSend],
  );

  return (
    <div className="flex flex-1 flex-col border-r border-border-primary bg-bg-card">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-border-divider px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-teal-light">
            <Bot size={18} className="text-teal" />
          </div>
          <div>
            <div className="font-heading text-[16px] font-medium text-text-primary">
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
      <PipelineStepper nodes={pipelineNodes} isStreaming={isStreaming} />

      {/* Messages */}
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto p-6">
        {messages.length === 0 && !isStreaming && (
          <SuggestionChips phase={phase} onSelect={handleChipSelect} />
        )}

        {messages.map((msg) => (
          <ChatMessageBubble key={msg.id} {...msg} />
        ))}

        {/* Streaming indicator */}
        {isStreaming && streamingText && (
          <div className="flex w-full gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-teal-light">
              <Bot size={16} className="text-teal" />
            </div>
            <div className="max-w-[520px] rounded-[12px_12px_12px_4px] border border-border-primary bg-bg-muted px-4 py-3">
              <p className="text-sm leading-relaxed text-text-primary whitespace-pre-wrap">
                {streamingText}
              </p>
            </div>
          </div>
        )}

        {isStreaming && !streamingText && (
          <div className="flex items-center gap-2 text-sm text-teal">
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
        className="flex shrink-0 items-center gap-3 border-t border-border-divider bg-bg-card px-6 py-4"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          maxLength={4000}
          disabled={isStreaming}
          className="h-11 flex-1 rounded-lg border border-border-primary bg-bg-muted px-4 text-sm text-text-primary placeholder:text-text-muted outline-none focus:border-teal"
        />
        <button
          type="submit"
          disabled={isStreaming || !input.trim()}
          aria-label="Send message"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-teal text-white transition-opacity disabled:opacity-40"
        >
          <Send size={18} />
        </button>
      </form>

      {/* Safety toast */}
      <SafetyToast toast={safetyToast} onDismiss={dismissToast} />
    </div>
  );
}
