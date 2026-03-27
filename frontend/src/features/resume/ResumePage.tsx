import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { api } from "../../shared/api/client";
import type {
  JDDocumentDetail,
  JDDocumentSummary,
  PartialProcessData,
  ProcessHistoryItem,
  ProcessJobStatus,
  ProjectMatchMapping,
  ResumeDocumentDetail,
  ResumeDocumentSummary,
} from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";

const resumeSchema = z.object({
  resumeText: z.string().optional(),
  jdText: z.string().optional(),
});

const POLL_INTERVAL_MS = 1200;
const DOCUMENT_POLL_INTERVAL_MS = 2500;

type ResumeFormValues = z.infer<typeof resumeSchema>;
type ResultTab = "report" | "resume";
type SourceMode = "library" | "manual";

export function ResumePage() {
  const [activeTab, setActiveTab] = useState<ResultTab>("report");
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [resumeSourceMode, setResumeSourceMode] = useState<SourceMode>("library");
  const [jdSourceMode, setJdSourceMode] = useState<SourceMode>("library");
  const [isHistoryModalOpen, setIsHistoryModalOpen] = useState(false);
  const [selectedHistoryJobId, setSelectedHistoryJobId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const { token } = useAuth();

  const form = useForm<ResumeFormValues>({
    resolver: zodResolver(resumeSchema),
    defaultValues: {
      resumeText: "",
      jdText: "",
    },
  });

  const resumeDocumentsQuery = useQuery<ResumeDocumentSummary[], Error>({
    queryKey: ["resume-documents", token],
    queryFn: () => api.getResumeDocuments(token!),
    enabled: Boolean(token),
    refetchInterval: (query) =>
      query.state.data?.some((item) => item.status === "processing") ? DOCUMENT_POLL_INTERVAL_MS : false,
  });

  const jdDocumentsQuery = useQuery<JDDocumentSummary[], Error>({
    queryKey: ["jd-documents", token],
    queryFn: () => api.getJdDocuments(token!),
    enabled: Boolean(token),
    refetchInterval: (query) =>
      query.state.data?.some((item) => item.status === "processing") ? DOCUMENT_POLL_INTERVAL_MS : false,
  });

  const activeResume = useMemo(
    () => resumeDocumentsQuery.data?.find((item) => item.is_active) ?? null,
    [resumeDocumentsQuery.data],
  );
  const activeJd = useMemo(() => jdDocumentsQuery.data?.find((item) => item.is_active) ?? null, [jdDocumentsQuery.data]);

  const activeResumeDetailQuery = useQuery<ResumeDocumentDetail, Error>({
    queryKey: ["resume-document-detail", token, activeResume?.id],
    queryFn: () => api.getResumeDocument(token!, activeResume!.id),
    enabled: Boolean(token && activeResume?.id),
  });

  const activeJdDetailQuery = useQuery<JDDocumentDetail, Error>({
    queryKey: ["jd-document-detail", token, activeJd?.id],
    queryFn: () => api.getJdDocument(token!, activeJd!.id),
    enabled: Boolean(token && activeJd?.id),
  });

  const startProcessMutation = useMutation<ProcessJobStatus, Error, ResumeFormValues>({
    mutationFn: async (values: ResumeFormValues) => {
      const resumeText = resolveResumeText({
        activeResume,
        activeResumeDetail: activeResumeDetailQuery.data,
        manualText: values.resumeText?.trim() ?? "",
        sourceMode: resumeSourceMode,
      });
      const jdText = resolveJdText({
        activeJd,
        activeJdDetail: activeJdDetailQuery.data,
        manualText: values.jdText?.trim() ?? "",
        sourceMode: jdSourceMode,
      });

      return api.startResumeProcess(token, {
        resume_text: resumeText,
        jd_text: jdText,
      });
    },
    onMutate: () => {
      setCurrentJobId(null);
      setActiveTab("report");
    },
    onSuccess: (job) => {
      queryClient.setQueryData(["resume-process", job.job_id], job);
      setCurrentJobId(job.job_id);
    },
  });

  const jobStatusQuery = useQuery<ProcessJobStatus, Error>({
    queryKey: ["resume-process", currentJobId],
    queryFn: () => api.getResumeProcessStatus(token, currentJobId!),
    enabled: currentJobId !== null,
    refetchInterval: (query) => {
      const current = query.state.data;
      if (!current) {
        return POLL_INTERVAL_MS;
      }

      return current.status === "success" || current.status === "error" ? false : POLL_INTERVAL_MS;
    },
  });

  const processStatus = currentJobId ? jobStatusQuery.data : undefined;
  const processData = processStatus?.data ?? null;
  const isProcessing =
    processStatus?.status === "running" ||
    startProcessMutation.isPending ||
    activeResumeDetailQuery.isFetching ||
    activeJdDetailQuery.isFetching;

  const errorMessage =
    startProcessMutation.error?.message ??
    jobStatusQuery.error?.message ??
    resumeDocumentsQuery.error?.message ??
    jdDocumentsQuery.error?.message ??
    activeResumeDetailQuery.error?.message ??
    activeJdDetailQuery.error?.message ??
    (processStatus?.status === "error" ? processStatus.error || processStatus.message : null);

  const historyQuery = useQuery<ProcessHistoryItem[], Error>({
    queryKey: ["resume-process-history", token],
    queryFn: () => api.getResumeProcessHistory(token!, 8),
    enabled: Boolean(token),
    refetchInterval: isProcessing ? 3000 : false,
  });

  const selectedHistoryItem = useMemo(
    () => historyQuery.data?.find((item) => item.job_id === selectedHistoryJobId) ?? null,
    [historyQuery.data, selectedHistoryJobId],
  );

  const selectedHistoryDetailQuery = useQuery<ProcessJobStatus, Error>({
    queryKey: ["resume-process-detail", token, selectedHistoryJobId],
    queryFn: () => api.getResumeProcessStatus(token, selectedHistoryJobId!),
    enabled: Boolean(token && isHistoryModalOpen && selectedHistoryJobId),
  });

  const deleteHistoryMutation = useMutation<{ success: boolean }, Error, string>({
    mutationFn: async (jobId) => api.deleteResumeProcessJob(token!, jobId),
    onSuccess: async (_result, deletedJobId) => {
      if (currentJobId === deletedJobId) {
        setCurrentJobId(null);
      }
      setSelectedHistoryJobId((current) => (current === deletedJobId ? null : current));
      await queryClient.invalidateQueries({ queryKey: ["resume-process-history", token] });
      await queryClient.invalidateQueries({ queryKey: ["resume-process", deletedJobId] });
      await queryClient.invalidateQueries({ queryKey: ["resume-process-detail", token, deletedJobId] });
    },
  });

  const deletingHistoryJobId = deleteHistoryMutation.isPending ? (deleteHistoryMutation.variables ?? null) : null;

  useEffect(() => {
    if (!currentJobId) {
      return;
    }

    if (processStatus?.status === "success" || processStatus?.status === "error") {
      void queryClient.invalidateQueries({ queryKey: ["resume-process-history", token] });
    }
  }, [currentJobId, processStatus?.status, queryClient, token]);

  useEffect(() => {
    if (!isHistoryModalOpen) {
      return;
    }

    const history = historyQuery.data ?? [];
    if (!history.length) {
      setSelectedHistoryJobId(null);
      return;
    }

    setSelectedHistoryJobId((current) =>
      current && history.some((item) => item.job_id === current) ? current : history[0].job_id,
    );
  }, [isHistoryModalOpen, historyQuery.data]);

  const handleOpenSelectedHistoryDetail = () => {
    if (!selectedHistoryJobId) {
      return;
    }

    if (selectedHistoryDetailQuery.data) {
      queryClient.setQueryData(["resume-process", selectedHistoryJobId], selectedHistoryDetailQuery.data);
    }

    setCurrentJobId(selectedHistoryJobId);
    setActiveTab("report");
    setIsHistoryModalOpen(false);
  };

  return (
    <section className="page-grid">
      <article className="page-card page-card-full">
        <div className="section-heading">
          <div>
            <span className="eyebrow">输入内容</span>
            <h3>开始分析</h3>
          </div>
          <div className="section-heading-actions">
            <button className="secondary-button" type="button" onClick={() => setIsHistoryModalOpen(true)}>
              历史记录
            </button>
            <Link className="secondary-button" to="/app/profile">
              打开个人信息
            </Link>
          </div>
        </div>

        <form className="form-stack" onSubmit={form.handleSubmit((values) => startProcessMutation.mutate(values))}>
          <div className="two-column-grid">
            <div className="soft-panel">
              <h4>简历来源</h4>
              <div className="segmented-control">
                <button
                  className={resumeSourceMode === "library" ? "segmented-active" : ""}
                  type="button"
                  onClick={() => setResumeSourceMode("library")}
                >
                  使用已保存资料
                </button>
                <button
                  className={resumeSourceMode === "manual" ? "segmented-active" : ""}
                  type="button"
                  onClick={() => setResumeSourceMode("manual")}
                >
                  临时粘贴
                </button>
              </div>

              {resumeSourceMode === "library" ? (
                <LibrarySourcePanel
                  ctaHref="/app/profile"
                  ctaLabel="去个人信息查看简历"
                  error={activeResume?.status === "error" ? activeResume.error || "该简历解析失败，请重新处理。" : null}
                  isLoading={activeResumeDetailQuery.isLoading}
                  title={activeResume?.title || null}
                  status={activeResume?.status || null}
                />
              ) : (
                <label className="field">
                  <span>简历内容</span>
                  <textarea
                    rows={11}
                    placeholder="粘贴你的项目经历、实习经历、教育背景与技能信息。内容越完整，后续的映射和重写越稳定。"
                    {...form.register("resumeText")}
                  />
                </label>
              )}
            </div>

            <div className="soft-panel">
              <h4>JD 来源</h4>
              <div className="segmented-control">
                <button
                  className={jdSourceMode === "library" ? "segmented-active" : ""}
                  type="button"
                  onClick={() => setJdSourceMode("library")}
                >
                  使用已保存资料
                </button>
                <button
                  className={jdSourceMode === "manual" ? "segmented-active" : ""}
                  type="button"
                  onClick={() => setJdSourceMode("manual")}
                >
                  临时粘贴
                </button>
              </div>

              {jdSourceMode === "library" ? (
                <LibrarySourcePanel
                  ctaHref="/app/profile"
                  ctaLabel="去个人信息查看 JD"
                  error={activeJd?.status === "error" ? activeJd.error || "该 JD 解析失败，请重新处理。" : null}
                  isLoading={activeJdDetailQuery.isLoading}
                  title={activeJd?.title || null}
                  status={activeJd?.status || null}
                />
              ) : (
                <label className="field">
                  <span>岗位描述 JD</span>
                  <textarea
                    rows={11}
                    placeholder="粘贴岗位职责、任职要求、加分项等信息。建议保留原始 JD，不要先手动删改关键词。"
                    {...form.register("jdText")}
                  />
                </label>
              )}
            </div>
          </div>

          {errorMessage ? <div className="callout callout-danger">{errorMessage}</div> : null}

          <div className="action-row">
            <button className="primary-button" disabled={isProcessing} type="submit">
              {isProcessing ? "正在生成..." : "开始分析并优化"}
            </button>
          </div>
        </form>
      </article>

      {isHistoryModalOpen ? (
        <div className="history-modal-overlay" onClick={() => setIsHistoryModalOpen(false)}>
          <div className="history-modal" onClick={(event) => event.stopPropagation()}>
            <div className="history-modal-header">
              <div>
                <h4>历史记录</h4>
                <p>点击左侧记录可查看详情，再决定是否打开详细内容。</p>
              </div>
              <div className="history-modal-actions">
                <button className="text-button" type="button" onClick={() => historyQuery.refetch()}>
                  {historyQuery.isFetching ? "刷新中..." : "刷新"}
                </button>
                <button className="secondary-button" type="button" onClick={() => setIsHistoryModalOpen(false)}>
                  关闭
                </button>
              </div>
            </div>

            <div className="history-modal-content">
              <div className="history-modal-list">
                {historyQuery.error ? <div className="callout callout-danger">{historyQuery.error.message}</div> : null}

                {historyQuery.isLoading ? (
                  <p className="muted-text">正在加载历史记录...</p>
                ) : historyQuery.data?.length ? (
                  <div className="history-list">
                    {historyQuery.data.map((item) => {
                      const isActive = item.job_id === selectedHistoryJobId;
                      return (
                        <button
                          key={item.job_id}
                          className={`history-item ${isActive ? "history-item-active" : ""}`}
                          type="button"
                          onClick={() => {
                            setSelectedHistoryJobId(item.job_id);
                            deleteHistoryMutation.reset();
                          }}
                        >
                          <div className="history-item-main">
                            <div className="history-item-heading">
                              <strong>{item.headline}</strong>
                              <span className={`history-status history-status-${item.status}`}>{getHistoryStatusLabel(item)}</span>
                              {item.job_id === currentJobId ? <span className="history-current-tag">当前查看</span> : null}
                            </div>
                            {item.subtitle ? <p>{item.subtitle}</p> : null}
                          </div>
                          <div className="history-item-meta">
                            <span>{formatHistoryTime(item.updated_at)}</span>
                            <small>{item.progress}%</small>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div className="empty-state">
                    <strong>还没有历史任务</strong>
                    <p>提交一次简历优化后，这里会保留最近记录，方便你重新查看结果。</p>
                  </div>
                )}
              </div>

              <div className="history-modal-detail">
                {selectedHistoryItem ? (
                  <>
                    <div className="soft-panel">
                      <h4>{selectedHistoryItem.headline}</h4>
                      {selectedHistoryItem.subtitle ? <p>{selectedHistoryItem.subtitle}</p> : null}
                      <div className="history-modal-meta">
                        <span>状态：{getHistoryStatusLabel(selectedHistoryItem)}</span>
                        <span>阶段：{getProcessStageLabel(selectedHistoryItem.stage)}</span>
                        <span>进度：{selectedHistoryItem.progress}%</span>
                        <span>更新时间：{formatHistoryTime(selectedHistoryItem.updated_at)}</span>
                      </div>
                    </div>

                    {selectedHistoryDetailQuery.isLoading ? <p className="muted-text">正在加载任务详情...</p> : null}
                    {selectedHistoryDetailQuery.error ? (
                      <div className="callout callout-danger">{selectedHistoryDetailQuery.error.message}</div>
                    ) : null}
                    {selectedHistoryDetailQuery.data ? (
                      <div className="soft-panel">
                        <h4>任务消息</h4>
                        <p>{selectedHistoryDetailQuery.data.message}</p>
                        {selectedHistoryDetailQuery.data.error ? (
                          <p className="field-error">{selectedHistoryDetailQuery.data.error}</p>
                        ) : null}
                      </div>
                    ) : null}
                    {deleteHistoryMutation.error ? (
                      <div className="callout callout-danger">{deleteHistoryMutation.error.message}</div>
                    ) : null}

                    <div className="history-modal-actions">
                      <button className="primary-button" type="button" onClick={handleOpenSelectedHistoryDetail}>
                        查看详细内容
                      </button>
                      <button
                        className="text-button text-button-danger"
                        type="button"
                        disabled={selectedHistoryItem.status === "running" || deleteHistoryMutation.isPending}
                        onClick={() => deleteHistoryMutation.mutate(selectedHistoryItem.job_id)}
                      >
                        {deletingHistoryJobId === selectedHistoryItem.job_id ? "删除中..." : "删除记录"}
                      </button>
                    </div>
                    {selectedHistoryItem.status === "running" ? (
                      <p className="muted-text">运行中的任务暂不支持删除。</p>
                    ) : null}
                  </>
                ) : (
                  <div className="empty-state">
                    <strong>请选择一条记录</strong>
                    <p>选择后可查看任务状态、时间与详细内容，并支持删除记录。</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {processStatus ? <ProcessProgressCard status={processStatus} /> : null}

      {processData?.match_mapping ? (
        <ResumeResults
          activeTab={activeTab}
          isRewritePending={!processData.optimized_resume}
          processData={processData}
          onTabChange={setActiveTab}
        />
      ) : null}
    </section>
  );
}

function resolveResumeText({
  sourceMode,
  manualText,
  activeResume,
  activeResumeDetail,
}: {
  sourceMode: SourceMode;
  manualText: string;
  activeResume: ResumeDocumentSummary | null;
  activeResumeDetail: ResumeDocumentDetail | undefined;
}) {
  if (sourceMode === "manual") {
    if (!manualText) {
      throw new Error("请输入简历内容");
    }
    return manualText;
  }

  if (!activeResume) {
    throw new Error("还没有当前简历，请先去个人信息页查看并设置");
  }
  if (activeResume.status === "processing") {
    throw new Error("当前简历仍在解析中，请稍后再试");
  }
  if (activeResume.status === "error") {
    throw new Error(activeResume.error || "当前简历解析失败，请去个人信息页检查");
  }

  const sourceText = activeResumeDetail?.source_text?.trim();
  if (!sourceText) {
    throw new Error("未能读取当前简历内容，请稍后重试");
  }
  return sourceText;
}

function resolveJdText({
  sourceMode,
  manualText,
  activeJd,
  activeJdDetail,
}: {
  sourceMode: SourceMode;
  manualText: string;
  activeJd: JDDocumentSummary | null;
  activeJdDetail: JDDocumentDetail | undefined;
}) {
  if (sourceMode === "manual") {
    if (!manualText) {
      throw new Error("请输入岗位描述 JD");
    }
    return manualText;
  }

  if (!activeJd) {
    throw new Error("还没有当前 JD，请先去个人信息页查看并设置");
  }
  if (activeJd.status === "processing") {
    throw new Error("当前 JD 仍在解析中，请稍后再试");
  }
  if (activeJd.status === "error") {
    throw new Error(activeJd.error || "当前 JD 解析失败，请去个人信息页检查");
  }

  const sourceText = activeJdDetail?.source_text?.trim();
  if (!sourceText) {
    throw new Error("未能读取当前 JD 内容，请稍后重试");
  }
  return sourceText;
}

function getSourceAvailabilityLabel(status: ResumeDocumentSummary["status"] | JDDocumentSummary["status"] | null) {
  if (!status) {
    return "未配置";
  }
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

type LibrarySourcePanelProps = {
  title: string | null;
  status: ResumeDocumentSummary["status"] | JDDocumentSummary["status"] | null;
  error: string | null;
  isLoading: boolean;
  ctaHref: string;
  ctaLabel: string;
};

function LibrarySourcePanel({ title, status, error, isLoading, ctaHref, ctaLabel }: LibrarySourcePanelProps) {
  if (isLoading) {
    return <p className="muted-text">正在读取当前资料...</p>;
  }

  if (!title || !status) {
    return (
      <div className="empty-state">
        <strong>还没有可用资料</strong>
        <p>请先去个人信息页查看并选择一份当前使用的资料。</p>
        <Link className="secondary-button" to={ctaHref}>
          {ctaLabel}
        </Link>
      </div>
    );
  }

  return (
    <div className="library-source-card">
      <div className="library-source-card-header">
        <strong>{title}</strong>
        <span className={`history-status history-status-${toHistoryStatusVariant(status)}`}>
          {getSourceAvailabilityLabel(status)}
        </span>
      </div>
      <p className="muted-text">
        {status === "ready"
          ? "当前资料已可直接用于分析。"
          : status === "processing"
            ? "当前资料正在后台解析，完成后即可直接使用。"
            : "当前资料解析失败，请重新处理或更换资料。"}
      </p>
      {error ? <p className="field-error">{error}</p> : null}
      <Link className="text-button" to={ctaHref}>
        {ctaLabel}
      </Link>
    </div>
  );
}

function getHistoryStatusLabel(item: ProcessHistoryItem) {
  if (item.status === "success") {
    return "已完成";
  }

  if (item.status === "error") {
    return "失败";
  }

  if (item.stage === "rewriting") {
    return "重写中";
  }

  if (item.stage === "mapping") {
    return "映射中";
  }

  return "解析中";
}

function getProcessStageLabel(stage: ProcessHistoryItem["stage"]) {
  if (stage === "parsing") {
    return "解析中";
  }
  if (stage === "mapping") {
    return "映射中";
  }
  if (stage === "rewriting") {
    return "重写中";
  }
  if (stage === "done") {
    return "已完成";
  }
  return "失败";
}

function formatHistoryTime(value: string) {
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

type ProcessProgressCardProps = {
  status: ProcessJobStatus;
};

function ProcessProgressCard({ status }: ProcessProgressCardProps) {
  const steps = [
    { key: "parsing", label: "解析简历与 JD" },
    { key: "mapping", label: "生成匹配映射" },
    { key: "rewriting", label: "重写项目要点" },
    { key: "done", label: "结果完成" },
  ] as const;
  const currentIndex = steps.findIndex((item) => item.key === status.stage);

  return (
    <article className="page-card page-card-full process-progress-card">
      <div className="progress-header">
        <div>
          <span className="eyebrow">处理进度</span>
          <h3>{status.message}</h3>
        </div>
        <span className="progress-percentage">{status.progress}%</span>
      </div>

      <div className="progress-track" aria-hidden="true">
        <div className="progress-fill" style={{ width: `${status.progress}%` }} />
      </div>

      <div className="progress-stages">
        {steps.map((step, index) => {
          const isComplete = status.stage === "done" || (currentIndex >= 0 && index < currentIndex);
          const isActive = step.key === status.stage;

          return (
            <span
              key={step.key}
              className={`stage-pill ${isComplete ? "stage-pill-complete" : ""} ${isActive ? "stage-pill-active" : ""}`}
            >
              {step.label}
            </span>
          );
        })}
      </div>

      <p className="progress-note">{getProgressNote(status)}</p>
    </article>
  );
}

function getProgressNote(status: ProcessJobStatus) {
  if (status.status === "error" || status.stage === "error") {
    return "任务在处理中断。你可以从最近任务中重新打开它，或调整简历与 JD 后再次提交。";
  }

  if (status.stage === "parsing") {
    return "正在抽取简历与岗位描述中的关键信息，建立后续映射所需的结构化上下文。";
  }

  if (status.stage === "mapping") {
    return "正在判断你的项目经历分别对应 JD 的哪些要求，并识别强匹配点与风险点。";
  }

  if (status.stage === "rewriting") {
    return "匹配映射已经完成，正在把结果转成更适合投递的项目描述与技能表达。";
  }

  if (status.stage === "done") {
    return "结果已经准备好，可以切换标签查看匹配报告或优化后的简历内容。";
  }

  return "正在处理中。";
}

type ResumeResultsProps = {
  processData: PartialProcessData;
  activeTab: ResultTab;
  onTabChange: (tab: ResultTab) => void;
  isRewritePending: boolean;
};

function ResumeResults({ processData, activeTab, onTabChange, isRewritePending }: ResumeResultsProps) {
  const jdInfo = processData.jd_info;
  const matchMapping = processData.match_mapping;
  const optimizedResume = processData.optimized_resume;
  const compactProjectMappings =
    matchMapping?.project_mappings.map((mapping) => ({
      project_name: mapping.project_name,
      matched_requirements: mapping.matched_requirements.slice(0, 3),
      rewrite_focus: mapping.rewrite_focus.slice(0, 2),
      missing_or_unsupported_points: mapping.missing_or_unsupported_points.slice(0, 2),
    })) ?? [];

  return (
    <article className="page-card page-card-full">
      <div className="section-heading">
        <div>
          <span className="eyebrow">分析结果</span>
          <h3>结果查看</h3>
        </div>
      </div>

      <div className="tabs">
        <button className={activeTab === "report" ? "tab-active" : ""} type="button" onClick={() => onTabChange("report")}>
          匹配报告
        </button>
        <button className={activeTab === "resume" ? "tab-active" : ""} type="button" onClick={() => onTabChange("resume")}>
          优化简历
        </button>
      </div>

      {activeTab === "report" ? (
        <div className="stacked-panels">
          <div className="two-column-grid">
            <section className="soft-panel">
              <h4>建议定位句</h4>
              <p>{matchMapping?.candidate_positioning || "正在生成定位建议..."}</p>
            </section>

            <section className="soft-panel">
              <h4>JD 重点</h4>
              <InfoRow label="目标岗位">{jdInfo?.job_title || "未提取"}</InfoRow>
              <InfoRow label="业务方向">{jdInfo?.business_domain || "未提取"}</InfoRow>
              {jdInfo?.must_have_skills?.length ? (
                <div className="badge-cloud">
                  {jdInfo.must_have_skills.slice(0, 6).map((item) => (
                    <span key={item} className="soft-badge soft-badge-dark">
                      {item}
                    </span>
                  ))}
                </div>
              ) : null}
            </section>
          </div>

          <div className="two-column-grid">
            <section className="soft-panel">
              <h4>强匹配点</h4>
              <ResultList items={matchMapping?.strong_match_points?.slice(0, 3) ?? []} emptyText="暂未生成强匹配点。" />
            </section>

            <section className="soft-panel">
              <h4>风险点</h4>
              <ResultList items={matchMapping?.risk_points?.slice(0, 3) ?? []} emptyText="暂未识别明显风险点。" />
            </section>
          </div>

          <div className="two-column-grid">
            <section className="soft-panel">
              <h4>关键词策略</h4>
              {matchMapping?.keyword_strategy?.length ? (
                <div className="badge-cloud">
                  {matchMapping.keyword_strategy.slice(0, 5).map((item) => (
                    <span key={item} className="soft-badge">
                      {item}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="muted-text">暂未生成关键词策略。</p>
              )}
            </section>

            <section className="soft-panel">
              <h4>技能改写建议</h4>
              <ResultList
                items={optimizedResume?.skills_rewrite_suggestions?.slice(0, 3) ?? []}
                emptyText={isRewritePending ? "正在生成技能改写建议..." : "暂无技能改写建议。"}
              />
            </section>
          </div>

          <section className="soft-panel">
            <h4>项目映射</h4>
            {compactProjectMappings.length ? (
              <div className="compact-project-grid">
                {compactProjectMappings.map((mapping) => (
                  <CompactProjectCard key={mapping.project_name} mapping={mapping} />
                ))}
              </div>
            ) : (
              <p className="muted-text">暂未生成项目映射。</p>
            )}
          </section>
        </div>
      ) : null}

      {activeTab === "resume" ? (
        <div className="stacked-panels">
          {optimizedResume ? (
            <>
              {optimizedResume.summary_hook ? (
                <section className="soft-panel">
                  <h4>开头摘要</h4>
                  <p>{optimizedResume.summary_hook}</p>
                </section>
              ) : null}

              {(optimizedResume.optimized_projects ?? []).map((project) => (
                <section key={project.original_project_name} className="soft-panel">
                  <h4>{project.original_project_name}</h4>
                  {project.project_positioning ? <p>{project.project_positioning}</p> : null}
                  <ResultList items={project.optimized_bullets} emptyText="暂无优化后的要点。" />
                </section>
              ))}
            </>
          ) : (
            <section className="soft-panel loading-panel">
              <h4>正在生成优化简历</h4>
              <p>匹配映射已完成，正在把结果转成更适合投递的项目描述与要点表达。</p>
            </section>
          )}
        </div>
      ) : null}
    </article>
  );
}

type CompactProjectCardProps = {
  mapping: Pick<ProjectMatchMapping, "project_name" | "matched_requirements" | "rewrite_focus" | "missing_or_unsupported_points">;
};

function CompactProjectCard({ mapping }: CompactProjectCardProps) {
  return (
    <article className="compact-project-card">
      <h5>{mapping.project_name}</h5>

      {mapping.matched_requirements.length ? (
        <div className="badge-cloud badge-cloud-tight">
          {mapping.matched_requirements.map((item) => (
            <span key={`${mapping.project_name}-${item}`} className="soft-badge soft-badge-dark">
              {item}
            </span>
          ))}
        </div>
      ) : null}

      {mapping.rewrite_focus.length ? (
        <>
          <strong>重点改写方向</strong>
          <ResultList items={mapping.rewrite_focus} />
        </>
      ) : null}

      {mapping.missing_or_unsupported_points.length ? (
        <>
          <strong>需谨慎处理</strong>
          <ResultList items={mapping.missing_or_unsupported_points} />
        </>
      ) : null}
    </article>
  );
}

type ResultListProps = {
  items: string[];
  emptyText?: string;
};

function ResultList({ items, emptyText = "暂无内容。" }: ResultListProps) {
  if (!items.length) {
    return <p className="muted-text">{emptyText}</p>;
  }

  return (
    <ul className="feature-list">
      {items.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  );
}

type InfoRowProps = {
  label: string;
  children: ReactNode;
};

function InfoRow({ label, children }: InfoRowProps) {
  return (
    <p>
      <strong>{label}：</strong>
      {children}
    </p>
  );
}
