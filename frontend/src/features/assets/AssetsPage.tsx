import type { ChangeEvent } from "react";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import type { JDDocumentSummary, ResumeDocumentSummary } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";

const DOCUMENT_POLL_INTERVAL_MS = 2500;

export function AssetsPage() {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [jdTitle, setJdTitle] = useState("");
  const [jdText, setJdText] = useState("");
  const [resumeUploadError, setResumeUploadError] = useState("");

  const resumesQuery = useQuery<ResumeDocumentSummary[], Error>({
    queryKey: ["resume-documents", token],
    queryFn: () => api.getResumeDocuments(token!),
    enabled: Boolean(token),
    refetchInterval: (query) =>
      query.state.data?.some((item) => item.status === "processing") ? DOCUMENT_POLL_INTERVAL_MS : false,
  });

  const jdsQuery = useQuery<JDDocumentSummary[], Error>({
    queryKey: ["jd-documents", token],
    queryFn: () => api.getJdDocuments(token!),
    enabled: Boolean(token),
    refetchInterval: (query) =>
      query.state.data?.some((item) => item.status === "processing") ? DOCUMENT_POLL_INTERVAL_MS : false,
  });

  const invalidateDocuments = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["resume-documents", token] }),
      queryClient.invalidateQueries({ queryKey: ["jd-documents", token] }),
    ]);
  };

  const uploadResumeMutation = useMutation<
    ResumeDocumentSummary,
    Error,
    {
      file: File;
      title?: string;
    }
  >({
    mutationFn: async ({ file, title }) => api.uploadResumeDocument(token!, file, title),
    onSuccess: async () => {
      setResumeUploadError("");
      await invalidateDocuments();
    },
  });

  const setActiveResumeMutation = useMutation<ResumeDocumentSummary, Error, number>({
    mutationFn: async (resumeId) => api.updateResumeDocument(token!, resumeId, { is_active: true }),
    onSuccess: invalidateDocuments,
  });

  const reprocessResumeMutation = useMutation<ResumeDocumentSummary, Error, number>({
    mutationFn: async (resumeId) => api.reprocessResumeDocument(token!, resumeId),
    onSuccess: invalidateDocuments,
  });

  const deleteResumeMutation = useMutation<{ success: boolean }, Error, number>({
    mutationFn: async (resumeId) => api.deleteResumeDocument(token!, resumeId),
    onSuccess: invalidateDocuments,
  });

  const createJdMutation = useMutation<
    JDDocumentSummary,
    Error,
    {
      title: string;
      source_text: string;
    }
  >({
    mutationFn: async (payload) => api.createJdDocument(token!, payload),
    onSuccess: async () => {
      setJdTitle("");
      setJdText("");
      await invalidateDocuments();
    },
  });

  const setActiveJdMutation = useMutation<JDDocumentSummary, Error, number>({
    mutationFn: async (jdId) => api.updateJdDocument(token!, jdId, { is_active: true }),
    onSuccess: invalidateDocuments,
  });

  const reprocessJdMutation = useMutation<JDDocumentSummary, Error, number>({
    mutationFn: async (jdId) => api.reprocessJdDocument(token!, jdId),
    onSuccess: invalidateDocuments,
  });

  const deleteJdMutation = useMutation<{ success: boolean }, Error, number>({
    mutationFn: async (jdId) => api.deleteJdDocument(token!, jdId),
    onSuccess: invalidateDocuments,
  });

  const resumeLimitReached = (resumesQuery.data?.length ?? 0) >= 3;
  const jdLimitReached = (jdsQuery.data?.length ?? 0) >= 3;

  const activeResume = useMemo(
    () => resumesQuery.data?.find((item) => item.is_active) ?? null,
    [resumesQuery.data],
  );
  const activeJd = useMemo(() => jdsQuery.data?.find((item) => item.is_active) ?? null, [jdsQuery.data]);

  async function handleResumeUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    setResumeUploadError("");

    if (!file) {
      return;
    }

    try {
      await uploadResumeMutation.mutateAsync({ file });
    } catch (error) {
      setResumeUploadError(error instanceof Error ? error.message : "简历上传失败");
    }
  }

  function handleCreateJd() {
    if (!jdTitle.trim() || !jdText.trim()) {
      return;
    }

    createJdMutation.mutate({
      title: jdTitle.trim(),
      source_text: jdText.trim(),
    });
  }

  return (
    <section className="page-grid">
      <article className="page-card page-card-full library-hero-card">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Library</span>
            <h3>资料库</h3>
          </div>
        </div>

        <div className="library-hero-grid">
          <div className="metric-card">
            <span>当前简历</span>
            <strong>{activeResume?.title || "未选择"}</strong>
            <small>{activeResume ? getDocumentStatusText(activeResume.status) : "去下方上传或选择一份简历"}</small>
          </div>
          <div className="metric-card">
            <span>当前 JD</span>
            <strong>{activeJd?.title || "未选择"}</strong>
            <small>{activeJd ? getDocumentStatusText(activeJd.status) : "去下方保存或选择一份 JD"}</small>
          </div>
          <div className="metric-card">
            <span>说明</span>
            <strong>每类最多 3 份</strong>
            <small>简历优化和后续模拟面试都会优先读取当前激活的资料</small>
          </div>
        </div>
      </article>

      <article className="page-card page-card-lg">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Resumes</span>
            <h3>已保存简历</h3>
          </div>
          <label className={`secondary-button upload-inline-button ${resumeLimitReached ? "button-disabled" : ""}`}>
            上传 PDF
            <input
              accept=".pdf"
              disabled={resumeLimitReached || uploadResumeMutation.isPending}
              type="file"
              onChange={handleResumeUpload}
            />
          </label>
        </div>

        <p className="muted-text">
          上传后会先保存文本，再在后台生成结构化结果和面试快照。只要状态变成“可用”，就能在简历优化里直接使用。
        </p>

        {resumeLimitReached ? <div className="callout">已达到 3 份简历上限，如需上传新简历请先删除旧简历。</div> : null}
        {resumeUploadError ? <div className="callout callout-danger">{resumeUploadError}</div> : null}
        {resumesQuery.error ? <div className="callout callout-danger">{resumesQuery.error.message}</div> : null}

        {resumesQuery.data?.length ? (
          <div className="document-list">
            {resumesQuery.data.map((item) => (
              <DocumentCard
                key={item.id}
                charCount={item.char_count}
                error={item.error}
                filename={item.source_filename || undefined}
                isActive={item.is_active}
                isBusy={
                  setActiveResumeMutation.isPending ||
                  reprocessResumeMutation.isPending ||
                  deleteResumeMutation.isPending
                }
                status={item.status}
                subtitle={item.source_filename || "已提取文本保存"}
                title={item.title}
                updatedAt={item.updated_at}
                onDelete={() => deleteResumeMutation.mutate(item.id)}
                onReprocess={() => reprocessResumeMutation.mutate(item.id)}
                onSetActive={() => setActiveResumeMutation.mutate(item.id)}
              />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <strong>还没有保存过简历</strong>
            <p>上传 PDF 后，这里会保留你的简历列表，并允许你切换当前使用的版本。</p>
          </div>
        )}
      </article>

      <article className="page-card">
        <div className="section-heading">
          <div>
            <span className="eyebrow">New JD</span>
            <h3>保存岗位描述</h3>
          </div>
        </div>

        <div className="form-stack">
          <label className="field">
            <span>JD 标题</span>
            <input
              maxLength={120}
              placeholder="例如：AI 应用开发实习生"
              value={jdTitle}
              onChange={(event) => setJdTitle(event.target.value)}
            />
          </label>
          <label className="field">
            <span>JD 内容</span>
            <textarea
              rows={12}
              placeholder="粘贴岗位职责、任职要求、加分项等内容"
              value={jdText}
              onChange={(event) => setJdText(event.target.value)}
            />
          </label>
          {jdLimitReached ? <div className="callout">已达到 3 份 JD 上限，如需新增请先删除旧 JD。</div> : null}
          {createJdMutation.error ? <div className="callout callout-danger">{createJdMutation.error.message}</div> : null}
          <button
            className="primary-button"
            disabled={jdLimitReached || createJdMutation.isPending || !jdTitle.trim() || !jdText.trim()}
            type="button"
            onClick={handleCreateJd}
          >
            {createJdMutation.isPending ? "保存中..." : "保存 JD"}
          </button>
        </div>
      </article>

      <article className="page-card page-card-full">
        <div className="section-heading">
          <div>
            <span className="eyebrow">JDs</span>
            <h3>已保存 JD</h3>
          </div>
        </div>

        {jdsQuery.error ? <div className="callout callout-danger">{jdsQuery.error.message}</div> : null}

        {jdsQuery.data?.length ? (
          <div className="document-list">
            {jdsQuery.data.map((item) => (
              <DocumentCard
                key={item.id}
                charCount={item.char_count}
                error={item.error}
                isActive={item.is_active}
                isBusy={setActiveJdMutation.isPending || reprocessJdMutation.isPending || deleteJdMutation.isPending}
                status={item.status}
                subtitle="已保存的岗位描述"
                title={item.title}
                updatedAt={item.updated_at}
                onDelete={() => deleteJdMutation.mutate(item.id)}
                onReprocess={() => reprocessJdMutation.mutate(item.id)}
                onSetActive={() => setActiveJdMutation.mutate(item.id)}
              />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <strong>还没有保存过 JD</strong>
            <p>把常投岗位的 JD 存下来，后续简历优化和模拟面试就能直接复用。</p>
          </div>
        )}
      </article>
    </section>
  );
}

type DocumentCardProps = {
  title: string;
  subtitle: string;
  status: ResumeDocumentSummary["status"] | JDDocumentSummary["status"];
  updatedAt: string;
  charCount: number;
  isActive: boolean;
  isBusy: boolean;
  error?: string | null;
  filename?: string;
  onSetActive: () => void;
  onReprocess: () => void;
  onDelete: () => void;
};

function DocumentCard({
  title,
  subtitle,
  status,
  updatedAt,
  charCount,
  isActive,
  isBusy,
  error,
  filename,
  onSetActive,
  onReprocess,
  onDelete,
}: DocumentCardProps) {
  return (
    <article className={`document-card ${isActive ? "document-card-active" : ""}`}>
      <div className="document-card-main">
        <div className="document-card-header">
          <div>
            <strong>{title}</strong>
            <p>{subtitle}</p>
          </div>
          <div className="document-card-badges">
            <span className={`history-status history-status-${toHistoryStatusVariant(status)}`}>
              {getDocumentStatusText(status)}
            </span>
            {isActive ? <span className="history-current-tag">当前使用</span> : null}
          </div>
        </div>

        <div className="document-card-meta">
          {filename ? <span>来源文件：{filename}</span> : null}
          <span>文本长度：{charCount} 字</span>
          <span>更新时间：{formatDocumentTime(updatedAt)}</span>
        </div>

        {error ? <p className="field-error">{error}</p> : null}
      </div>

      <div className="document-card-actions">
        {!isActive ? (
          <button className="secondary-button" disabled={isBusy} type="button" onClick={onSetActive}>
            设为当前
          </button>
        ) : null}
        {status !== "processing" ? (
          <button className="secondary-button" disabled={isBusy} type="button" onClick={onReprocess}>
            重新解析
          </button>
        ) : null}
        <button className="text-button text-button-danger" disabled={isBusy} type="button" onClick={onDelete}>
          删除
        </button>
      </div>
    </article>
  );
}

function getDocumentStatusText(status: ResumeDocumentSummary["status"] | JDDocumentSummary["status"]) {
  if (status === "ready") {
    return "可用";
  }
  if (status === "error") {
    return "失败";
  }
  return "解析中";
}

function toHistoryStatusVariant(status: ResumeDocumentSummary["status"] | JDDocumentSummary["status"]) {
  if (status === "ready") {
    return "success";
  }
  if (status === "error") {
    return "error";
  }
  return "running";
}

function formatDocumentTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "时间未知";
  }

  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
