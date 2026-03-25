import type {
  ChatRequest,
  ChatResponse,
  ParsePdfResponse,
  ProcessResponse,
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
  processResume: (payload: { resume_text: string; jd_text: string }) =>
    request<ProcessResponse>("/api/v1/process", {
      method: "POST",
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
