import { AlertCircle, Bot, Wrench } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "../types";

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// --- Bot message ---
function BotMessage({ content, timestamp }: ChatMessageType) {
  return (
    <div className="flex w-full gap-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-badge-bg">
        <Bot size={18} className="text-blue-badge-text" />
      </div>
      <div className="max-w-[520px]">
        <div className="rounded-bl-xl rounded-br-xl rounded-tr-xl bg-bg-subtle px-4 py-3">
          <p className="text-sm leading-relaxed text-text-primary whitespace-pre-wrap">
            {content}
          </p>
        </div>
        <span className="mt-1 block text-[11px] text-text-muted">
          {formatTime(timestamp)}
        </span>
      </div>
    </div>
  );
}

// --- User message ---
function UserMessage({ content, timestamp }: ChatMessageType) {
  return (
    <div className="flex w-full justify-end">
      <div className="max-w-[280px]">
        <div className="rounded-bl-xl rounded-br-xl rounded-tl-xl bg-text-primary px-4 py-3">
          <p className="text-sm leading-relaxed text-white whitespace-pre-wrap">
            {content}
          </p>
        </div>
        <span className="mt-1 block text-right text-[11px] text-text-secondary">
          {formatTime(timestamp)}
        </span>
      </div>
    </div>
  );
}

// --- Tool call message ---
function ToolMessage({
  content,
  toolName,
  timestamp,
}: ChatMessageType) {
  return (
    <div className="flex w-full gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-badge-bg">
        <Wrench size={16} className="text-amber-badge-text" />
      </div>
      <div className="max-w-[520px]">
        <div className="rounded-bl-xl rounded-br-xl rounded-tr-xl bg-amber-badge-bg px-4 py-3">
          <span className="font-mono text-xs font-semibold text-amber-badge-text">
            Tool: {toolName}
          </span>
          <p className="mt-1.5 text-sm leading-relaxed text-text-primary whitespace-pre-wrap">
            {content}
          </p>
        </div>
        <span className="mt-1 block text-[11px] text-text-muted">
          {formatTime(timestamp)}
        </span>
      </div>
    </div>
  );
}

// --- Error message ---
function ErrorMessage({ content, timestamp }: ChatMessageType) {
  return (
    <div className="flex w-full gap-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-badge-bg">
        <AlertCircle size={18} className="text-red-badge-text" />
      </div>
      <div className="max-w-[520px]">
        <div className="rounded-bl-xl rounded-br-xl rounded-tr-xl bg-red-badge-bg px-4 py-3">
          <p className="text-sm leading-relaxed text-red-badge-text">
            {content}
          </p>
        </div>
        <span className="mt-1 block text-[11px] text-text-muted">
          {formatTime(timestamp)}
        </span>
      </div>
    </div>
  );
}

// --- Dispatcher ---
export function ChatMessageBubble(props: ChatMessageType) {
  switch (props.role) {
    case "user":
      return <UserMessage {...props} />;
    case "tool":
      return <ToolMessage {...props} />;
    case "error":
      return <ErrorMessage {...props} />;
    default:
      return <BotMessage {...props} />;
  }
}
