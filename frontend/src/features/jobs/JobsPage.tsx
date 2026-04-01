import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../../shared/api/client";
import { useAuth } from "../../shared/auth/AuthContext";
import type { ChatResponse } from "../../shared/api/types";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

const starterPrompts = [
  "Help me find Python backend interview prep points.",
  "Give me a 2-week plan to improve resume-job matching.",
  "How should I answer common self-introduction questions?",
];

export function JobsPage() {
  const { token } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");

  const chatMutation = useMutation<ChatResponse, Error, { message: string }>({
    mutationFn: async (payload) => api.sendChat(token, payload),
    onSuccess: (response) => {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.reply,
        },
      ]);
    },
    onError: (error) => {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: error.message,
        },
      ]);
    },
  });

  const canSend = useMemo(() => draft.trim().length > 0 && !chatMutation.isPending, [draft, chatMutation.isPending]);

  function appendUserMessage(content: string) {
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: "user",
        content,
      },
    ]);
  }

  function sendMessage(message: string) {
    const cleanMessage = message.trim();
    if (!cleanMessage) {
      return;
    }

    appendUserMessage(cleanMessage);
    setDraft("");
    chatMutation.mutate({ message: cleanMessage });
  }

  return (
    <section className="chat-layout">
      <header className="chat-intro">
        <span className="eyebrow">Conversational Search</span>
        <h3>Chat with JobCopilot</h3>
        <p>Ask job-search and interview questions, and get practical guidance.</p>
      </header>

      {messages.length === 0 ? (
        <section className="starter-grid">
          {starterPrompts.map((prompt) => (
            <button className="starter-card" key={prompt} type="button" onClick={() => sendMessage(prompt)}>
              {prompt}
            </button>
          ))}
        </section>
      ) : null}

      <section className="chat-thread">
        {messages.map((message) => (
          <article
            className={`message-row ${message.role === "user" ? "message-row-user" : "message-row-assistant"}`}
            key={message.id}
          >
            <div className="message-avatar">{message.role === "user" ? "ME" : "AI"}</div>
            <div className="message-bubble">
              <p>{message.content}</p>
            </div>
          </article>
        ))}

        {chatMutation.isPending ? (
          <article className="message-row message-row-assistant">
            <div className="message-avatar">AI</div>
            <div className="message-bubble message-bubble-pending">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </article>
        ) : null}
      </section>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          sendMessage(draft);
        }}
      >
        <textarea
          rows={1}
          placeholder="Ask JobCopilot anything about jobs or interviews..."
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="primary-button" disabled={!canSend} type="submit">
          Send
        </button>
      </form>
    </section>
  );
}
