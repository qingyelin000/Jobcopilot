import type { ChangeEvent, ReactNode } from "react";
import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { api } from "../../shared/api/client";
import type { ProcessResponse, ProjectMatchMapping } from "../../shared/api/types";

const resumeSchema = z.object({
  jdText: z.string().min(20, "请输入至少 20 个字符的 JD"),
  resumeText: z.string().optional(),
});

type ResumeFormValues = z.infer<typeof resumeSchema>;
type ResultTab = "report" | "resume";

export function ResumePage() {
  const [sourceMode, setSourceMode] = useState<"text" | "pdf">("text");
  const [parsedResumeText, setParsedResumeText] = useState("");
  const [pdfFileName, setPdfFileName] = useState("");
  const [pdfError, setPdfError] = useState("");
  const [isParsingPdf, setIsParsingPdf] = useState(false);
  const [activeTab, setActiveTab] = useState<ResultTab>("report");

  const form = useForm<ResumeFormValues>({
    resolver: zodResolver(resumeSchema),
    defaultValues: {
      jdText: "",
      resumeText: "",
    },
  });

  const resultMutation = useMutation<ProcessResponse, Error, ResumeFormValues>({
    mutationFn: async (values: ResumeFormValues) => {
      const resumeText = sourceMode === "text" ? values.resumeText?.trim() ?? "" : parsedResumeText.trim();

      if (!resumeText) {
        throw new Error(sourceMode === "text" ? "请输入简历内容" : "请先上传并解析 PDF 简历");
      }

      return api.processResume({
        resume_text: resumeText,
        jd_text: values.jdText.trim(),
      });
    },
    onSuccess: () => {
      setActiveTab("report");
    },
  });

  const result = resultMutation.data;

  async function handlePdfUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setPdfError("");

    if (!file) {
      return;
    }

    setPdfFileName(file.name);
    setIsParsingPdf(true);

    try {
      const parsed = await api.parseResumePdf(file);
      if (!parsed.text.trim()) {
        throw new Error("PDF 已上传，但没有解析出可用文本");
      }

      setParsedResumeText(parsed.text);
    } catch (error) {
      setParsedResumeText("");
      setPdfError(error instanceof Error ? error.message : "PDF 解析失败");
    } finally {
      setIsParsingPdf(false);
    }
  }

  return (
    <section className="page-grid">
      <article className="page-card page-card-full">
        <form className="form-stack" onSubmit={form.handleSubmit((values) => resultMutation.mutate(values))}>
          <div className="segmented-control">
            <button
              className={sourceMode === "text" ? "segmented-active" : ""}
              type="button"
              onClick={() => setSourceMode("text")}
            >
              粘贴简历文本
            </button>
            <button
              className={sourceMode === "pdf" ? "segmented-active" : ""}
              type="button"
              onClick={() => setSourceMode("pdf")}
            >
              上传 PDF 简历
            </button>
          </div>

          {sourceMode === "text" ? (
            <label className="field">
              <span>简历内容</span>
              <textarea
                rows={12}
                placeholder="粘贴完整简历内容，建议保留项目经历、技能、教育背景等关键信息。"
                {...form.register("resumeText")}
              />
            </label>
          ) : (
            <div className="upload-panel">
              <label className="upload-dropzone">
                <input accept=".pdf" type="file" onChange={handlePdfUpload} />
                <strong>{pdfFileName || "点击上传 PDF 简历"}</strong>
                <span>系统会先提取文本，再进入后续分析流程。</span>
              </label>

              <div className="upload-status">
                {isParsingPdf ? <p>正在解析 PDF...</p> : null}
                {parsedResumeText ? <p>已提取 {parsedResumeText.length} 个字符</p> : null}
                {pdfError ? <p className="field-error">{pdfError}</p> : null}
              </div>

              {parsedResumeText ? (
                <details className="details-panel">
                  <summary>查看解析后的文本预览</summary>
                  <pre>{parsedResumeText.slice(0, 2000)}</pre>
                </details>
              ) : null}
            </div>
          )}

          <label className="field">
            <span>目标 JD</span>
            <textarea
              rows={12}
              placeholder="粘贴岗位描述，建议包含岗位职责、技术要求和业务场景。"
              {...form.register("jdText")}
            />
            {form.formState.errors.jdText ? (
              <small className="field-error">{form.formState.errors.jdText.message}</small>
            ) : null}
          </label>

          {resultMutation.error ? <div className="callout callout-danger">{resultMutation.error.message}</div> : null}

          <div className="action-row">
            <button className="primary-button" type="submit" disabled={resultMutation.isPending}>
              {resultMutation.isPending ? "生成中..." : "生成定制结果"}
            </button>
            <span className="muted-text">输出匹配摘要和可直接使用的项目 bullets。</span>
          </div>
        </form>
      </article>

      {result ? <ResumeResults result={result} activeTab={activeTab} onTabChange={setActiveTab} /> : null}
    </section>
  );
}

type ResumeResultsProps = {
  result: ProcessResponse;
  activeTab: ResultTab;
  onTabChange: (tab: ResultTab) => void;
};

function ResumeResults({ result, activeTab, onTabChange }: ResumeResultsProps) {
  const { jd_info, match_mapping, optimized_resume } = result.data;
  const compactProjectMappings = match_mapping.project_mappings.map((mapping) => ({
    project_name: mapping.project_name,
    matched_requirements: mapping.matched_requirements.slice(0, 3),
    rewrite_focus: mapping.rewrite_focus.slice(0, 2),
    missing_or_unsupported_points: mapping.missing_or_unsupported_points.slice(0, 2),
  }));

  return (
    <article className="page-card page-card-full">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Structured Result</span>
          <h3>分析结果</h3>
        </div>
      </div>

      <div className="tabs">
        <button className={activeTab === "report" ? "tab-active" : ""} onClick={() => onTabChange("report")} type="button">
          匹配摘要
        </button>
        <button className={activeTab === "resume" ? "tab-active" : ""} onClick={() => onTabChange("resume")} type="button">
          优化简历
        </button>
      </div>

      {activeTab === "report" ? (
        <div className="stacked-panels">
          <div className="two-column-grid">
            <section className="soft-panel">
              <h4>一句话判断</h4>
              <p>{match_mapping.candidate_positioning || "暂未生成定位描述。"}</p>
            </section>

            <section className="soft-panel">
              <h4>JD 重点</h4>
              <InfoRow label="岗位名称">{jd_info.job_title || "未识别"}</InfoRow>
              <InfoRow label="业务场景">{jd_info.business_domain || "未识别"}</InfoRow>
              {jd_info.must_have_skills.length ? (
                <div className="badge-cloud">
                  {jd_info.must_have_skills.slice(0, 6).map((item) => (
                    <span className="soft-badge soft-badge-dark" key={item}>
                      {item}
                    </span>
                  ))}
                </div>
              ) : null}
            </section>
          </div>

          <div className="two-column-grid">
            <section className="soft-panel">
              <h4>优先强调</h4>
              <ResultList items={match_mapping.strong_match_points.slice(0, 3)} emptyText="暂未提取到明显强项。" />
            </section>

            <section className="soft-panel">
              <h4>需要补位</h4>
              <ResultList items={match_mapping.risk_points.slice(0, 3)} emptyText="暂未提取到明显短板。" />
            </section>
          </div>

          <div className="two-column-grid">
            <section className="soft-panel">
              <h4>建议关键词</h4>
              {match_mapping.keyword_strategy.length ? (
                <div className="badge-cloud">
                  {match_mapping.keyword_strategy.slice(0, 5).map((item) => (
                    <span className="soft-badge" key={item}>
                      {item}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="muted-text">暂未生成关键词建议。</p>
              )}
            </section>

            <section className="soft-panel">
              <h4>技能区建议</h4>
              <ResultList
                items={optimized_resume.skills_rewrite_suggestions.slice(0, 3)}
                emptyText="暂未生成技能区建议。"
              />
            </section>
          </div>

          <section className="soft-panel">
            <h4>项目重点</h4>
            <div className="compact-project-grid">
              {compactProjectMappings.map((mapping) => (
                <CompactProjectCard key={mapping.project_name} mapping={mapping} />
              ))}
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "resume" ? (
        <div className="stacked-panels">
          {optimized_resume.summary_hook ? (
            <section className="soft-panel">
              <h4>建议定位句</h4>
              <p>{optimized_resume.summary_hook}</p>
            </section>
          ) : null}

          {optimized_resume.optimized_projects.map((project) => (
            <section className="soft-panel" key={project.original_project_name}>
              <h4>{project.original_project_name}</h4>
              {project.project_positioning ? <p>{project.project_positioning}</p> : null}
              <ResultList items={project.optimized_bullets} emptyText="暂未生成该项目的 bullets。" />
            </section>
          ))}
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
            <span className="soft-badge soft-badge-dark" key={`${mapping.project_name}-${item}`}>
              {item}
            </span>
          ))}
        </div>
      ) : null}

      {mapping.rewrite_focus.length ? (
        <>
          <strong>建议突出</strong>
          <ResultList items={mapping.rewrite_focus} />
        </>
      ) : null}

      {mapping.missing_or_unsupported_points.length ? (
        <>
          <strong>谨慎处理</strong>
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

function ResultList({ items, emptyText = "暂无数据。" }: ResultListProps) {
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
