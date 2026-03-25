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
  "帮我找上海的 Python 后端岗位",
  "我想找偏 AI 应用工程师的职位",
  "给我几条适合社招后端工程师的检索建议",
];

export function JobsPage() {
  const { token, user, setUser } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [pendingPrompt, setPendingPrompt] = useState("");
  const [consentScope, setConsentScope] = useState<"once" | "always">("once");

  const chatMutation = useMutation<
    ChatResponse,
    Error,
    {
      message: string;
      location_consent?: boolean;
      consent_scope?: "once" | "always";
      latitude?: number;
      longitude?: number;
    }
  >({
    mutationFn: async (payload: {
      message: string;
      location_consent?: boolean;
      consent_scope?: "once" | "always";
      latitude?: number;
      longitude?: number;
    }) => api.sendChat(token, payload),
    onSuccess: (response, variables) => {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.reply,
        },
      ]);

      if (response.need_location_consent) {
        setPendingPrompt(variables.message);
      } else {
        setPendingPrompt("");
      }

      if (variables.location_consent && variables.consent_scope === "always" && user) {
        setUser({ ...user, location_consent: true });
      }
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

  async function handleLocationRetry() {
    if (!pendingPrompt) {
      return;
    }

    if (!navigator.geolocation) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "当前浏览器不支持定位能力。",
        },
      ]);
      return;
    }

    try {
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 12000,
          maximumAge: 60000,
        });
      });

      chatMutation.mutate({
        message: pendingPrompt,
        location_consent: true,
        consent_scope: consentScope,
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      });
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: error instanceof Error ? error.message : "定位授权失败",
        },
      ]);
    }
  }

  return (
    <section className="chat-layout">
      <header className="chat-intro">
        <span className="eyebrow">Conversational Search</span>
        <h3>今天想找什么工作</h3>
        <p>输入岗位、城市或偏好，系统会整理结果并继续追问。</p>
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
            <div className="message-avatar">{message.role === "user" ? "你" : "AI"}</div>
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

      {pendingPrompt ? (
        <section className="callout">
          <strong>附近职位检索需要定位授权</strong>
          <p>你可以只授权这一次，也可以记住当前的授权选择。</p>
          <div className="segmented-control">
            <button
              className={consentScope === "once" ? "segmented-active" : ""}
              type="button"
              onClick={() => setConsentScope("once")}
            >
              仅本次允许
            </button>
            <button
              className={consentScope === "always" ? "segmented-active" : ""}
              type="button"
              onClick={() => setConsentScope("always")}
            >
              始终允许
            </button>
          </div>
          <div className="action-row">
            <button className="primary-button" type="button" onClick={handleLocationRetry}>
              授权定位并继续
            </button>
            <span className="muted-text">
              当前账户持久化状态：{user?.location_consent ? "已允许" : "未允许"}
            </span>
          </div>
        </section>
      ) : null}

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          sendMessage(draft);
        }}
      >
        <textarea
          rows={1}
          placeholder="给 JobCopilot 发送消息..."
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="primary-button" disabled={!canSend} type="submit">
          发送
        </button>
      </form>
    </section>
  );
}
