export type TokenResponse = {
  access_token: string;
  token_type: string;
};

export type UserProfile = {
  id: number;
  username: string;
  location_consent: boolean;
  full_name?: string | null;
  email?: string | null;
  phone?: string | null;
  city?: string | null;
  target_role?: string | null;
  profile_summary?: string | null;
};

export type DocumentStatus = "processing" | "ready" | "error";

export type ResumeDocumentSummary = {
  id: number;
  title: string;
  source_filename?: string | null;
  status: DocumentStatus;
  error?: string | null;
  is_active: boolean;
  char_count: number;
  created_at: string;
  updated_at: string;
};

export type ResumeDocumentDetail = ResumeDocumentSummary & {
  source_text: string;
};

export type JDDocumentSummary = {
  id: number;
  title: string;
  status: DocumentStatus;
  error?: string | null;
  is_active: boolean;
  char_count: number;
  created_at: string;
  updated_at: string;
};

export type JDDocumentDetail = JDDocumentSummary & {
  source_text: string;
};

export type ProjectExperience = {
  project_name: string;
  role: string;
  description: string;
  tech_stack: string[];
};

export type UserInfo = {
  name?: string | null;
  education: string;
  global_tech_stack: string[];
  projects: ProjectExperience[];
};

export type JDInfo = {
  job_title: string;
  company_name?: string | null;
  must_have_skills: string[];
  nice_to_have_skills: string[];
  core_responsibilities: string[];
  business_domain: string;
};

export type ProjectMatchMapping = {
  project_name: string;
  matched_requirements: string[];
  evidence_points: string[];
  missing_or_unsupported_points: string[];
  rewrite_focus: string[];
  narrative_strategy: string;
  honesty_risks: string[];
};

export type ResumeJDMapping = {
  candidate_positioning: string;
  strong_match_points: string[];
  risk_points: string[];
  keyword_strategy: string[];
  project_mappings: ProjectMatchMapping[];
};

export type OptimizedProject = {
  original_project_name: string;
  project_positioning: string;
  optimized_bullets: string[];
};

export type OptimizedResume = {
  summary_hook: string;
  skills_rewrite_suggestions: string[];
  optimized_projects: OptimizedProject[];
};

export type ProcessData = {
  user_info: UserInfo;
  jd_info: JDInfo;
  match_mapping: ResumeJDMapping;
  optimized_resume: OptimizedResume;
};

export type PartialProcessData = Partial<ProcessData>;

export type ProcessResponse = {
  status: string;
  data: ProcessData;
};

export type ProcessJobStatus = {
  job_id: string;
  status: "running" | "success" | "error";
  stage: "parsing" | "mapping" | "rewriting" | "done" | "error";
  progress: number;
  message: string;
  data?: PartialProcessData | null;
  error?: string | null;
};

export type ProcessHistoryItem = {
  job_id: string;
  status: "running" | "success" | "error";
  stage: "parsing" | "mapping" | "rewriting" | "done" | "error";
  progress: number;
  message: string;
  headline: string;
  subtitle?: string | null;
  created_at: string;
  updated_at: string;
};

export type ChatResponse = {
  reply: string;
  need_location_consent?: boolean;
};

export type ChatRequest = {
  message: string;
  location_consent?: boolean;
  consent_scope?: "once" | "always";
  user_city?: string;
  latitude?: number;
  longitude?: number;
};

export type ParsePdfResponse = {
  filename: string;
  page_count: number;
  char_count: number;
  text: string;
};
