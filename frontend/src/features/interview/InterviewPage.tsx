import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../shared/api/client";
import { useAuth } from "../../shared/auth/AuthContext";
import type {
  InterviewAnswerResponse,
  InterviewSessionStartResponse,
  InterviewSessionSummaryResponse,
  InterviewSummary,
  JDDocumentSummary,
  ResumeDocumentSummary,
} from "../../shared/api/types";

type ThreadMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

function formatSummary(summary: InterviewSummary): string {
  return [
    `总体得分: ${summary.overall_score.toFixed(1)}`,
    `准确性: ${summary.dimension_scores.accuracy.toFixed(1)}`,
    `深度: ${summary.dimension_scores.depth.toFixed(1)}`,
    `结构化: ${summary.dimension_scores.structure.toFixed(1)}`,
    `简历匹配度: ${summary.dimension_scores.resume_fit.toFixed(1)}`,
    summary.summary,
  ].join("\n");
}

function pickDefaultReadyDocumentId<T extends { id: number; is_active: boolean }>(documents: T[]): number | null {
  if (!documents.length) {
    return null;
  }
  const active = documents.find((item) => item.is_active);
  if (active) {
    return active.id;
  }
  return documents[0].id;
}

export function InterviewPage() {
  const { token } = useAuth();

  const [selectedResumeId, setSelectedResumeId] = useState<number | null>(null);
  const [selectedJdId, setSelectedJdId] = useState<number | null>(null);

  const [session, setSession] = useState<InterviewSessionStartResponse | null>(null);
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [answerDraft, setAnswerDraft] = useState("");
  const [summary, setSummary] = useState<InterviewSummary | null>(null);

  const resumeDocumentsQuery = useQuery<ResumeDocumentSummary[], Error>({
    queryKey: ["resume-documents", token],
    queryFn: () => api.getResumeDocuments(token!),
    enabled: Boolean(token),
  });

  const jdDocumentsQuery = useQuery<JDDocumentSummary[], Error>({
    queryKey: ["jd-documents", token],
    queryFn: () => api.getJdDocuments(token!),
    enabled: Boolean(token),
  });

  const readyResumes = useMemo(
    () => (resumeDocumentsQuery.data ?? []).filter((item) => item.status === "ready"),
    [resumeDocumentsQuery.data],
  );
  const readyJds = useMemo(
    () => (jdDocumentsQuery.data ?? []).filter((item) => item.status === "ready"),
    [jdDocumentsQuery.data],
  );

  useEffect(() => {
    setSelectedResumeId((current) => {
      if (current && readyResumes.some((item) => item.id === current)) {
        return current;
      }
      return pickDefaultReadyDocumentId(readyResumes);
    });
  }, [readyResumes]);

  useEffect(() => {
    setSelectedJdId((current) => {
      if (current && readyJds.some((item) => item.id === current)) {
        return current;
      }
      return pickDefaultReadyDocumentId(readyJds);
    });
  }, [readyJds]);

  const startMutation = useMutation<InterviewSessionStartResponse, Error, void>({
    mutationFn: async () => {
      if (selectedResumeId === null || selectedJdId === null) {
        throw new Error("请选择可用的简历和 JD 后再开始。");
      }
      return api.startInterviewSession(token, {
        resume_id: selectedResumeId,
        jd_id: selectedJdId,
        backend: "v2",
      });
    },
    onSuccess: (response) => {
      setSession(response);
      setSummary(null);
      setAnswerDraft("");
      setMessages([
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: `第${response.current_round}轮\n${response.question.question_text}`,
        },
      ]);
    },
  });

  const answerMutation = useMutation<InterviewAnswerResponse, Error, { answerText: string }>({
    mutationFn: async ({ answerText }) => {
      if (!session) {
        throw new Error("面试会话尚未开始。");
      }
      return api.answerInterviewSession(token, session.session_id, {
        answer_text: answerText,
      });
    },
    onSuccess: (response, variables) => {
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "user", content: variables.answerText },
      ]);

      const assistantBlocks: string[] = [];
      if (response.next_question?.question_text) {
        assistantBlocks.push(`第${response.current_round}轮\n${response.next_question.question_text}`);
      }
      if (response.summary) {
        assistantBlocks.push(`面试总结:\n${formatSummary(response.summary)}`);
        setSummary(response.summary);
      }
      if (!assistantBlocks.length) {
        assistantBlocks.push("已记录本轮回答。");
      }

      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: assistantBlocks.join("\n\n"),
        },
      ]);

      setAnswerDraft("");
      setSession((current) =>
        current
          ? {
              ...current,
              status: response.status,
              current_round: response.current_round,
              max_rounds: response.max_rounds,
              question: response.next_question ?? current.question,
            }
          : current,
      );
    },
  });

  const finishMutation = useMutation<InterviewSessionSummaryResponse, Error>({
    mutationFn: async () => {
      if (!session) {
        throw new Error("面试会话尚未开始。");
      }
      return api.finishInterviewSession(token, session.session_id);
    },
    onSuccess: (response) => {
      setSummary(response.summary);
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: `面试总结:\n${formatSummary(response.summary)}`,
        },
      ]);
      setSession((current) =>
        current
          ? {
              ...current,
              status: response.status,
              current_round: response.current_round,
              max_rounds: response.max_rounds,
            }
          : current,
      );
    },
  });

  const canStartSession =
    selectedResumeId !== null &&
    selectedJdId !== null &&
    !startMutation.isPending &&
    !resumeDocumentsQuery.isLoading &&
    !jdDocumentsQuery.isLoading;

  const canSendAnswer =
    Boolean(session) &&
    session?.status !== "done" &&
    answerDraft.trim().length > 0 &&
    !answerMutation.isPending;

  const setupError =
    resumeDocumentsQuery.error?.message ??
    jdDocumentsQuery.error?.message ??
    (!resumeDocumentsQuery.isLoading && !readyResumes.length
      ? "暂无可用简历，请先在个人中心上传并等待解析完成。"
      : null) ??
    (!jdDocumentsQuery.isLoading && !readyJds.length ? "暂无可用 JD，请先在个人中心创建并等待解析完成。" : null);

  return (
    <section className="chat-layout">
      <header className="chat-intro">
        <span className="eyebrow">模拟面试</span>
        <h3>面试官 + 评估官</h3>
        <p>面试模式下，系统会在后台逐轮评估并驱动下一题，过程不展示分数，结束后统一给出总结。</p>
      </header>

      {!session ? (
        <section className="callout interview-setup">
          <strong>会话设置</strong>
          <div className="interview-setup-grid">
            <label className="interview-field">
              <span>选择简历</span>
              <select
                value={selectedResumeId ?? ""}
                onChange={(event) => setSelectedResumeId(event.target.value ? Number(event.target.value) : null)}
                disabled={!readyResumes.length || startMutation.isPending}
              >
                {!readyResumes.length ? <option value="">暂无可用简历</option> : null}
                {readyResumes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                    {item.is_active ? "（当前使用）" : ""}
                  </option>
                ))}
              </select>
            </label>

            <label className="interview-field">
              <span>选择 JD</span>
              <select
                value={selectedJdId ?? ""}
                onChange={(event) => setSelectedJdId(event.target.value ? Number(event.target.value) : null)}
                disabled={!readyJds.length || startMutation.isPending}
              >
                {!readyJds.length ? <option value="">暂无可用 JD</option> : null}
                {readyJds.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                    {item.is_active ? "（当前使用）" : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="action-row">
            <button className="primary-button" type="button" onClick={() => startMutation.mutate()} disabled={!canStartSession}>
              {startMutation.isPending ? "启动中..." : "开始面试"}
            </button>
            <Link className="secondary-button" to="/app/profile">
              管理简历和 JD
            </Link>
          </div>

          {resumeDocumentsQuery.isLoading || jdDocumentsQuery.isLoading ? (
            <div className="callout">正在加载你的简历和 JD...</div>
          ) : null}
          {setupError ? <div className="callout callout-danger">{setupError}</div> : null}
          {startMutation.error ? <div className="callout callout-danger">{startMutation.error.message}</div> : null}
        </section>
      ) : (
        <>
          <section className="callout">
            <div className="action-row">
              <button
                className="secondary-button"
                type="button"
                onClick={() => finishMutation.mutate()}
                disabled={finishMutation.isPending || session.status === "done"}
              >
                {finishMutation.isPending ? "结束中..." : "结束面试"}
              </button>
              <button
                className="ghost-button"
                type="button"
                onClick={() => {
                  setSession(null);
                  setMessages([]);
                  setSummary(null);
                }}
              >
                新建会话
              </button>
            </div>
            {finishMutation.error ? <div className="callout callout-danger">{finishMutation.error.message}</div> : null}
          </section>

          <section className="chat-thread">
            {messages.map((message) => (
              <article
                className={`message-row ${message.role === "user" ? "message-row-user" : "message-row-assistant"}`}
                key={message.id}
              >
                <div className="message-avatar">{message.role === "user" ? "我" : "AI"}</div>
                <div className="message-bubble">
                  <p style={{ whiteSpace: "pre-wrap" }}>{message.content}</p>
                </div>
              </article>
            ))}

            {answerMutation.isPending ? (
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
              if (!canSendAnswer) {
                return;
              }
              answerMutation.mutate({ answerText: answerDraft.trim() });
            }}
          >
            <textarea
              rows={2}
              value={answerDraft}
              onChange={(event) => setAnswerDraft(event.target.value)}
              placeholder={session.status === "done" ? "会话已结束" : "输入你的面试回答..."}
              disabled={session.status === "done"}
            />
            <button className="primary-button" disabled={!canSendAnswer} type="submit">
              提交回答
            </button>
          </form>

          {answerMutation.error ? <div className="callout callout-danger">{answerMutation.error.message}</div> : null}

          {summary ? (
            <section className="callout interview-summary-panel">
              <strong>总结快照</strong>
              <p style={{ whiteSpace: "pre-wrap" }}>{formatSummary(summary)}</p>
            </section>
          ) : null}
        </>
      )}
    </section>
  );
}
