import { AlertCircle, Bot, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { ChatMessage as ChatMessageType } from "../types";

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// --- Bot message ---
function BotMessage({ content, timestamp }: ChatMessageType) {
  return (
    <div className="flex w-full gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-teal-light">
        <Bot size={16} className="text-teal" />
      </div>
      <div className="max-w-[520px]">
        <div className="rounded-[12px_12px_12px_4px] border border-border-primary bg-bg-muted px-4 py-3">
          <div className="prose text-sm leading-relaxed text-text-primary">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        </div>
        <span className="mt-1 block font-mono text-[10px] text-text-muted">
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
      <div className="max-w-[400px]">
        <div className="rounded-[12px_12px_4px_12px] bg-teal px-4 py-3">
          <p className="text-sm leading-relaxed text-white whitespace-pre-wrap">
            {content}
          </p>
        </div>
        <span className="mt-1 block text-right font-mono text-[10px] text-text-muted">
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
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-orange-light">
        <Wrench size={16} className="text-orange" />
      </div>
      <div className="max-w-[520px]">
        <div className="rounded-[12px_12px_12px_4px] bg-orange-light px-4 py-3">
          <span className="font-mono text-xs font-semibold text-orange">
            Tool: {toolName}
          </span>
          <p className="mt-1.5 text-sm leading-relaxed text-text-primary whitespace-pre-wrap">
            {content}
          </p>
        </div>
        <span className="mt-1 block font-mono text-[10px] text-text-muted">
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
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-light">
        <AlertCircle size={16} className="text-red" />
      </div>
      <div className="max-w-[520px]">
        <div className="rounded-[12px_12px_12px_4px] bg-red-light px-4 py-3">
          <p className="text-sm leading-relaxed text-red">
            {content}
          </p>
        </div>
        <span className="mt-1 block font-mono text-[10px] text-text-muted">
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
