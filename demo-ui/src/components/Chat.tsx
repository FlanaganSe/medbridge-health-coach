import { useState, useRef, useEffect, useCallback } from "react";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface ChatProps {
  patientId: string;
  tenantId: string;
}

function makeId(): string {
  return crypto.randomUUID();
}

/**
 * Line-buffered SSE parser that handles chunks spanning reads.
 * Calls `onEvent` for each complete `data: ...` line.
 */
function createSSEParser(onEvent: (data: string) => void) {
  let buffer = "";
  return {
    feed(chunk: string) {
      buffer += chunk;
      const lines = buffer.split("\n");
      // Keep the last (possibly incomplete) line in the buffer
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          onEvent(line.slice(6));
        }
      }
    },
    flush() {
      if (buffer.startsWith("data: ")) {
        onEvent(buffer.slice(6));
      }
      buffer = "";
    },
  };
}

export function Chat({ patientId, tenantId }: ChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Reset messages when patient changes
  useEffect(() => {
    setMessages([]);
  }, [patientId]);

  const sendMessage = useCallback(async () => {
    if (!input.trim() || loading) return;

    const userMessage = input.trim();
    setInput("");
    setMessages((prev) => [
      ...prev,
      { id: makeId(), role: "user", content: userMessage },
    ]);
    setLoading(true);

    try {
      const response = await fetch("/v1/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Patient-ID": patientId,
          "X-Tenant-ID": tenantId,
        },
        body: JSON.stringify({ message: userMessage }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        setMessages((prev) => [
          ...prev,
          {
            id: makeId(),
            role: "assistant",
            content: `[Error ${response.status}: ${errorText}]`,
          },
        ]);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let assistantContent = "";

      const parser = createSSEParser((data) => {
        try {
          const parsed = JSON.parse(data);
          if (parsed.type === "done") return;
          if (parsed.type === "error") {
            assistantContent += `[Error: ${parsed.message}]`;
            return;
          }
          // Extract outbound_message from any node's state update
          for (const nodeData of Object.values(parsed)) {
            const node = nodeData as Record<string, unknown>;
            if (
              node?.outbound_message &&
              typeof node.outbound_message === "string"
            ) {
              assistantContent += node.outbound_message;
            }
          }
        } catch {
          // Skip unparseable events
        }
      });

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        parser.feed(decoder.decode(value, { stream: true }));
      }
      parser.flush();

      if (assistantContent) {
        setMessages((prev) => [
          ...prev,
          { id: makeId(), role: "assistant", content: assistantContent },
        ]);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "assistant",
          content: `[Connection error: ${err}]`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, patientId, tenantId]);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: 16,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {messages.length === 0 && !loading && (
          <div
            style={{
              textAlign: "center",
              color: "#9ca3af",
              padding: "48px 16px",
            }}
          >
            Send a message to start the conversation.
          </div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            style={{
              alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
              background: msg.role === "user" ? "#3b82f6" : "#f3f4f6",
              color: msg.role === "user" ? "white" : "black",
              padding: "8px 12px",
              borderRadius: 12,
              maxWidth: "70%",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {msg.content}
          </div>
        ))}
        {loading && (
          <div style={{ color: "#9ca3af", fontStyle: "italic" }}>
            Thinking...
          </div>
        )}
        <div ref={messagesEnd} />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          sendMessage();
        }}
        style={{
          display: "flex",
          gap: 8,
          padding: 16,
          borderTop: "1px solid #e5e7eb",
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          disabled={loading}
          style={{
            flex: 1,
            padding: "8px 12px",
            border: "1px solid #d1d5db",
            borderRadius: 8,
            outline: "none",
          }}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          style={{
            padding: "8px 16px",
            background:
              loading || !input.trim() ? "#93c5fd" : "#3b82f6",
            color: "white",
            border: "none",
            borderRadius: 8,
            cursor: loading || !input.trim() ? "default" : "pointer",
          }}
        >
          Send
        </button>
      </form>
    </div>
  );
}
