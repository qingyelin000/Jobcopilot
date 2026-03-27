import type {
  ChatRequest,
  ChatResponse,
  JDDocumentDetail,
  JDDocumentSummary,
  ParsePdfResponse,
  ProcessHistoryItem,
  ProcessJobStatus,
  ProcessResponse,
  ResumeDocumentDetail,
  ResumeDocumentSummary,
  TokenResponse,
  UserProfile,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

type RequestOptions = RequestInit & {
  token?: string | null;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);

  if (!headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;

    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        message = payload.detail;
      }
    } catch {
      // Ignore malformed error payloads.
    }

    throw new Error(message);
  }

  return (await response.json()) as T;
}

export const api = {
  register: (payload: { username: string; password: string }) =>
    request<TokenResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  login: (payload: { username: string; password: string }) =>
    request<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getMe: (token: string) =>
    request<UserProfile>("/api/v1/users/me", {
      token,
    }),
  updatePreferences: (token: string, payload: { location_consent: boolean }) =>
    request<UserProfile>("/api/v1/users/me/preferences", {
      method: "PATCH",
      token,
      body: JSON.stringify(payload),
    }),
  updateProfile: (
    token: string,
    payload: {
      full_name?: string | null;
      email?: string | null;
      phone?: string | null;
      city?: string | null;
      target_role?: string | null;
      profile_summary?: string | null;
    },
  ) =>
    request<UserProfile>("/api/v1/users/me/profile", {
      method: "PATCH",
      token,
      body: JSON.stringify(payload),
    }),
  changePassword: (token: string, payload: { current_password: string; new_password: string }) =>
    request<{ success: boolean; message: string }>("/api/v1/users/me/password", {
      method: "PATCH",
      token,
      body: JSON.stringify(payload),
    }),
  getResumeDocuments: (token: string) =>
    request<ResumeDocumentSummary[]>("/api/v1/resumes", {
      token,
    }),
  getResumeDocument: (token: string, resumeId: number) =>
    request<ResumeDocumentDetail>(`/api/v1/resumes/${resumeId}`, {
      token,
    }),
  uploadResumeDocument: (token: string, file: File, title?: string) => {
    const formData = new FormData();
    formData.append("file", file);
    if (title?.trim()) {
      formData.append("title", title.trim());
    }

    return request<ResumeDocumentSummary>("/api/v1/resumes/upload-pdf", {
      method: "POST",
      token,
      body: formData,
    });
  },
  updateResumeDocument: (
    token: string,
    resumeId: number,
    payload: { title?: string; source_text?: string; is_active?: boolean },
  ) =>
    request<ResumeDocumentSummary>(`/api/v1/resumes/${resumeId}`, {
      method: "PATCH",
      token,
      body: JSON.stringify(payload),
    }),
  reprocessResumeDocument: (token: string, resumeId: number) =>
    request<ResumeDocumentSummary>(`/api/v1/resumes/${resumeId}/reprocess`, {
      method: "POST",
      token,
    }),
  deleteResumeDocument: (token: string, resumeId: number) =>
    request<{ success: boolean }>(`/api/v1/resumes/${resumeId}`, {
      method: "DELETE",
      token,
    }),
  getJdDocuments: (token: string) =>
    request<JDDocumentSummary[]>("/api/v1/jds", {
      token,
    }),
  getJdDocument: (token: string, jdId: number) =>
    request<JDDocumentDetail>(`/api/v1/jds/${jdId}`, {
      token,
    }),
  createJdDocument: (
    token: string,
    payload: { title: string; source_text: string; is_active?: boolean },
  ) =>
    request<JDDocumentSummary>("/api/v1/jds", {
      method: "POST",
      token,
      body: JSON.stringify(payload),
    }),
  updateJdDocument: (
    token: string,
    jdId: number,
    payload: { title?: string; source_text?: string; is_active?: boolean },
  ) =>
    request<JDDocumentSummary>(`/api/v1/jds/${jdId}`, {
      method: "PATCH",
      token,
      body: JSON.stringify(payload),
    }),
  reprocessJdDocument: (token: string, jdId: number) =>
    request<JDDocumentSummary>(`/api/v1/jds/${jdId}/reprocess`, {
      method: "POST",
      token,
    }),
  deleteJdDocument: (token: string, jdId: number) =>
    request<{ success: boolean }>(`/api/v1/jds/${jdId}`, {
      method: "DELETE",
      token,
    }),
  startResumeProcess: (token: string | null, payload: { resume_text: string; jd_text: string }) =>
    request<ProcessJobStatus>("/api/v1/process/start", {
      method: "POST",
      token,
      body: JSON.stringify(payload),
    }),
  getResumeProcessStatus: (token: string | null, jobId: string) =>
    request<ProcessJobStatus>(`/api/v1/process/${jobId}`, {
      token,
    }),
  deleteResumeProcessJob: (token: string, jobId: string) =>
    request<{ success: boolean }>(`/api/v1/process/${jobId}`, {
      method: "DELETE",
      token,
    }),
  getResumeProcessHistory: (token: string, limit = 8) =>
    request<ProcessHistoryItem[]>(`/api/v1/process/history?limit=${limit}`, {
      token,
    }),
  processResume: (token: string | null, payload: { resume_text: string; jd_text: string }) =>
    request<ProcessResponse>("/api/v1/process", {
      method: "POST",
      token,
      body: JSON.stringify(payload),
    }),
  parseResumePdf: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);

    return request<ParsePdfResponse>("/api/v1/resume/parse-pdf", {
      method: "POST",
      body: formData,
    });
  },
  sendChat: (token: string | null, payload: ChatRequest) =>
    request<ChatResponse>("/api/v1/chat", {
      method: "POST",
      token,
      body: JSON.stringify(payload),
    }),
};
