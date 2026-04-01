import type { ChangeEvent } from "react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { api } from "../../shared/api/client";
import type { JDDocumentSummary, ResumeDocumentSummary } from "../../shared/api/types";
import { useAuth } from "../../shared/auth/AuthContext";

const DOCUMENT_POLL_INTERVAL_MS = 2500;

type PasswordFormValues = {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
};

export function ProfilePage() {
  const { token, user, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [passwordSuccessMessage, setPasswordSuccessMessage] = useState("");
  const [isPasswordPanelOpen, setIsPasswordPanelOpen] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [resumeUploadError, setResumeUploadError] = useState("");
  const [isCreateJdOpen, setIsCreateJdOpen] = useState(false);
  const [jdTitle, setJdTitle] = useState("");
  const [jdText, setJdText] = useState("");

  const form = useForm<PasswordFormValues>({
    defaultValues: {
      currentPassword: "",
      newPassword: "",
      confirmPassword: "",
    },
  });

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

  const resumeLimitReached = (resumesQuery.data?.length ?? 0) >= 3;
  const jdLimitReached = (jdsQuery.data?.length ?? 0) >= 3;

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

  const deleteResumeMutation = useMutation<{ success: boolean }, Error, number>({
    mutationFn: async (resumeId) => api.deleteResumeDocument(token!, resumeId),
    onSuccess: invalidateDocuments,
  });

  const setActiveResumeMutation = useMutation<ResumeDocumentSummary, Error, number>({
    mutationFn: async (resumeId) => api.updateResumeDocument(token!, resumeId, { is_active: true }),
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
      setIsCreateJdOpen(false);
      await invalidateDocuments();
    },
  });

  const deleteJdMutation = useMutation<{ success: boolean }, Error, number>({
    mutationFn: async (jdId) => api.deleteJdDocument(token!, jdId),
    onSuccess: invalidateDocuments,
  });

  const setActiveJdMutation = useMutation<JDDocumentSummary, Error, number>({
    mutationFn: async (jdId) => api.updateJdDocument(token!, jdId, { is_active: true }),
    onSuccess: invalidateDocuments,
  });

  const changePasswordMutation = useMutation<
    { success: boolean; message: string },
    Error,
    { current_password: string; new_password: string }
  >({
    mutationFn: async (payload) => api.changePassword(token!, payload),
    onSuccess: (result) => {
      setPasswordSuccessMessage(result.message || "密码修改成功");
      form.reset({
        currentPassword: "",
        newPassword: "",
        confirmPassword: "",
      });
      setIsPasswordPanelOpen(false);
    },
  });

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

  const handlePasswordSubmit = form.handleSubmit((values) => {
    setPasswordSuccessMessage("");
    form.clearErrors();

    if (values.newPassword.length < 6) {
      form.setError("newPassword", {
        type: "minLength",
        message: "新密码至少 6 个字符",
      });
      return;
    }

    if (values.newPassword !== values.confirmPassword) {
      form.setError("confirmPassword", {
        type: "validate",
        message: "两次输入的新密码不一致",
      });
      return;
    }

    changePasswordMutation.mutate({
      current_password: values.currentPassword,
      new_password: values.newPassword,
    });
  });

  const handleTogglePasswordPanel = () => {
    setPasswordSuccessMessage("");
    setIsPasswordPanelOpen((current) => {
      const next = !current;
      if (!next) {
        form.reset();
        form.clearErrors();
      }
      return next;
    });
  };

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <section className="page-grid">
      <article className="page-card page-card-full">
        <div className="section-heading">
          <div>
            <h3>个人信息</h3>
          </div>
        </div>

        <div className="profile-summary-list">
          <div className="profile-summary-row">
            <span>用户名</span>
            <strong>{user?.username || "未登录"}</strong>
          </div>
        </div>

        <div className="profile-inline-panel">
          <button
            className="secondary-button"
            type="button"
            onClick={handleTogglePasswordPanel}
          >
            {isPasswordPanelOpen ? "收起" : "更改密码"}
          </button>

          {isPasswordPanelOpen ? (
            <div className="soft-panel profile-inline-form">
              <form className="form-stack" onSubmit={handlePasswordSubmit} noValidate>
                <label className="field">
                  <span>当前密码</span>
                  <input
                    type="password"
                    placeholder="请输入当前密码"
                    autoComplete="current-password"
                    {...form.register("currentPassword", {
                      required: "请输入当前密码",
                    })}
                  />
                  {form.formState.errors.currentPassword ? (
                    <small className="field-error">{form.formState.errors.currentPassword.message}</small>
                  ) : null}
                </label>

                <label className="field">
                  <span>新密码</span>
                  <input
                    type="password"
                    placeholder="请输入新密码"
                    autoComplete="new-password"
                    {...form.register("newPassword", {
                      required: "请输入新密码",
                    })}
                  />
                  {form.formState.errors.newPassword ? (
                    <small className="field-error">{form.formState.errors.newPassword.message}</small>
                  ) : null}
                </label>

                <label className="field">
                  <span>确认新密码</span>
                  <input
                    type="password"
                    placeholder="请再次输入新密码"
                    autoComplete="new-password"
                    {...form.register("confirmPassword", {
                      required: "请再次输入新密码",
                    })}
                  />
                  {form.formState.errors.confirmPassword ? (
                    <small className="field-error">{form.formState.errors.confirmPassword.message}</small>
                  ) : null}
                </label>

                {changePasswordMutation.error ? (
                  <div className="callout callout-danger">{changePasswordMutation.error.message}</div>
                ) : null}

                <div className="action-row">
                  <button className="primary-button" type="submit" disabled={changePasswordMutation.isPending}>
                    {changePasswordMutation.isPending ? "提交中..." : "确认更改"}
                  </button>
                </div>
              </form>
            </div>
          ) : null}

          {passwordSuccessMessage ? <div className="callout">{passwordSuccessMessage}</div> : null}

          <button className="text-button text-button-danger" type="button" onClick={() => setShowLogoutConfirm(true)}>
            退出登录
          </button>
        </div>
      </article>

      <article className="page-card page-card-lg">
        <div className="section-heading">
          <div>
            <h3>已保存的简历</h3>
          </div>
          <label className={`secondary-button upload-inline-button ${resumeLimitReached ? "button-disabled" : ""}`}>
            上传简历
            <input
              accept=".pdf"
              disabled={resumeLimitReached || uploadResumeMutation.isPending}
              type="file"
              onChange={handleResumeUpload}
            />
          </label>
        </div>

        {resumeLimitReached ? <div className="callout">已达到 3 份简历上限，请先删除旧简历再次上传</div> : null}
        {resumeUploadError ? <div className="callout callout-danger">{resumeUploadError}</div> : null}
        {resumesQuery.error ? <div className="callout callout-danger">{resumesQuery.error.message}</div> : null}

        {resumesQuery.data?.length ? (
          <div className="document-list">
            {resumesQuery.data.map((item) => {
              const isResumeBusy =
                setActiveResumeMutation.isPending || deleteResumeMutation.isPending || uploadResumeMutation.isPending;
              const canSelectResume = !item.is_active && !isResumeBusy;

              return (
                <article
                  key={item.id}
                  className={`document-card ${item.is_active ? "document-card-active" : ""} ${canSelectResume ? "document-card-selectable" : ""}`}
                  role={canSelectResume ? "button" : undefined}
                  tabIndex={canSelectResume ? 0 : undefined}
                  onClick={() => {
                    if (canSelectResume) {
                      setActiveResumeMutation.mutate(item.id);
                    }
                  }}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) {
                      return;
                    }
                    if (canSelectResume && (event.key === "Enter" || event.key === " ")) {
                      event.preventDefault();
                      setActiveResumeMutation.mutate(item.id);
                    }
                  }}
                >
                  <div className="document-card-main">
                    <div className="document-card-header">
                      <div>
                        <strong>{item.title}</strong>
                        <p>{item.source_filename || "已提取文本保存"}</p>
                      </div>
                      <div className="document-card-badges">
                        <span className={`history-status history-status-${toHistoryStatusVariant(item.status)}`}>
                          {getDocumentStatusText(item.status)}
                        </span>
                        {item.is_active ? <span className="history-current-tag">当前使用</span> : null}
                      </div>
                    </div>

                    <div className="document-card-meta">
                      <span>文本长度：{item.char_count} 字</span>
                      <span>更新时间：{formatDocumentTime(item.updated_at)}</span>
                    </div>

                    {item.error ? <p className="field-error">{item.error}</p> : null}
                  </div>

                  <div className="document-card-actions">
                    <button
                      className="text-button text-button-danger"
                      type="button"
                      disabled={isResumeBusy}
                      onClick={(event) => {
                        event.stopPropagation();
                        deleteResumeMutation.mutate(item.id);
                      }}
                    >
                      删除
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">
            <strong>还没有保存过简历</strong>
            <p>当前账号下暂无简历记录。</p>
          </div>
        )}
      </article>

      <article className="page-card">
        <div className="section-heading">
          <div>
            <h3>已保存的 JD</h3>
          </div>
          <button
            className="secondary-button"
            type="button"
            disabled={jdLimitReached}
            onClick={() => setIsCreateJdOpen((current) => !current)}
          >
            {isCreateJdOpen ? "收起" : "新增 JD"}
          </button>
        </div>

        {isCreateJdOpen ? (
          <div className="soft-panel profile-inline-form">
            <div className="form-stack">
              <label className="field">
                <span>JD 标题</span>
                <input
                  maxLength={120}
                  placeholder="例如：腾讯AI 应用开发实习生"
                  value={jdTitle}
                  onChange={(event) => setJdTitle(event.target.value)}
                />
              </label>
              <label className="field">
                <span>JD 内容</span>
                <textarea
                  rows={10}
                  placeholder="粘贴岗位职责、任职要求、加分项等内容"
                  value={jdText}
                  onChange={(event) => setJdText(event.target.value)}
                />
              </label>
              {createJdMutation.error ? <div className="callout callout-danger">{createJdMutation.error.message}</div> : null}
              <div className="action-row">
                <button
                  className="primary-button"
                  type="button"
                  disabled={jdLimitReached || createJdMutation.isPending || !jdTitle.trim() || !jdText.trim()}
                  onClick={handleCreateJd}
                >
                  {createJdMutation.isPending ? "保存中..." : "保存 JD"}
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => {
                    setJdTitle("");
                    setJdText("");
                    setIsCreateJdOpen(false);
                  }}
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {jdLimitReached ? <div className="callout">已达到 3 份 JD 上限，请先删除旧 JD再上传</div> : null}
        {jdsQuery.error ? <div className="callout callout-danger">{jdsQuery.error.message}</div> : null}

        {jdsQuery.data?.length ? (
          <div className="document-list">
            {jdsQuery.data.map((item) => {
              const isJdBusy = setActiveJdMutation.isPending || deleteJdMutation.isPending || createJdMutation.isPending;
              const canSelectJd = !item.is_active && !isJdBusy;

              return (
                <article
                  key={item.id}
                  className={`document-card ${item.is_active ? "document-card-active" : ""} ${canSelectJd ? "document-card-selectable" : ""}`}
                  role={canSelectJd ? "button" : undefined}
                  tabIndex={canSelectJd ? 0 : undefined}
                  onClick={() => {
                    if (canSelectJd) {
                      setActiveJdMutation.mutate(item.id);
                    }
                  }}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) {
                      return;
                    }
                    if (canSelectJd && (event.key === "Enter" || event.key === " ")) {
                      event.preventDefault();
                      setActiveJdMutation.mutate(item.id);
                    }
                  }}
                >
                  <div className="document-card-main">
                    <div className="document-card-header">
                      <div>
                        <strong>{item.title}</strong>
                        <p>已保存的岗位描述</p>
                      </div>
                      <div className="document-card-badges">
                        <span className={`history-status history-status-${toHistoryStatusVariant(item.status)}`}>
                          {getDocumentStatusText(item.status)}
                        </span>
                        {item.is_active ? <span className="history-current-tag">当前使用</span> : null}
                      </div>
                    </div>

                    <div className="document-card-meta">
                      <span>文本长度：{item.char_count} 字</span>
                      <span>更新时间：{formatDocumentTime(item.updated_at)}</span>
                    </div>

                    {item.error ? <p className="field-error">{item.error}</p> : null}
                  </div>

                  <div className="document-card-actions">
                    <button
                      className="text-button text-button-danger"
                      type="button"
                      disabled={isJdBusy}
                      onClick={(event) => {
                        event.stopPropagation();
                        deleteJdMutation.mutate(item.id);
                      }}
                    >
                      删除
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">
            <strong>还没有保存过 JD</strong>
            <p>当前账号下暂无 JD 记录。</p>
          </div>
        )}
      </article>

      {showLogoutConfirm ? (
        <div className="confirm-modal-overlay" onClick={() => setShowLogoutConfirm(false)}>
          <div className="confirm-modal" onClick={(event) => event.stopPropagation()}>
            <h4>确认退出登录？</h4>
            <p>退出后需要重新输入账号密码登录。</p>
            <div className="confirm-modal-actions">
              <button className="secondary-button" type="button" onClick={() => setShowLogoutConfirm(false)}>
                取消
              </button>
              <button className="primary-button" type="button" onClick={handleLogout}>
                确认退出
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
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
