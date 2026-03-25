import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";
import { useAuth } from "../../shared/auth/AuthContext";

const authSchema = z.object({
  username: z.string().min(3, "用户名至少 3 个字符"),
  password: z.string().min(6, "密码至少 6 个字符"),
});

type AuthFormValues = z.infer<typeof authSchema>;

export function LoginPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const { authenticate, token } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const redirectTarget = useMemo(() => {
    const state = location.state as { from?: string } | null;
    return state?.from || "/app/resume";
  }, [location.state]);

  const form = useForm<AuthFormValues>({
    resolver: zodResolver(authSchema),
    defaultValues: {
      username: "",
      password: "",
    },
  });

  const authMutation = useMutation<void, Error, AuthFormValues>({
    mutationFn: async (values: AuthFormValues) => {
      await authenticate(mode, values);
    },
    onSuccess: () => {
      navigate(redirectTarget, { replace: true });
    },
  });

  if (token) {
    return <Navigate to={redirectTarget} replace />;
  }

  return (
    <main className="auth-page">
      <section className="auth-hero">
        <span className="eyebrow">JobCopilot</span>
        <h1>开始你的求职工作流</h1>
        <p>登录后即可管理简历分析、岗位探索和个人偏好。</p>

        <div className="auth-feature-grid">
          <article className="auth-feature-card">
            <strong>Resume Engine</strong>
            <span>围绕目标 JD 输出分析、定制简历与求职信。</span>
          </article>
          <article className="auth-feature-card">
            <strong>Jobs Copilot</strong>
            <span>像聊天一样筛选岗位，支持定位授权和偏好保存。</span>
          </article>
          <article className="auth-feature-card">
            <strong>Account Center</strong>
            <span>集中管理账户信息、定位授权和个性化设置。</span>
          </article>
        </div>
      </section>

      <section className="auth-card">
        <div className="auth-mode-switch">
          <button
            className={mode === "login" ? "primary-button" : "ghost-button"}
            type="button"
            onClick={() => setMode("login")}
          >
            登录
          </button>
          <button
            className={mode === "register" ? "primary-button" : "ghost-button"}
            type="button"
            onClick={() => setMode("register")}
          >
            注册
          </button>
        </div>

        <form className="form-stack" onSubmit={form.handleSubmit((values) => authMutation.mutate(values))}>
          <label className="field">
            <span>用户名</span>
            <input placeholder="例如 zhangsan" {...form.register("username")} />
            {form.formState.errors.username ? (
              <small className="field-error">{form.formState.errors.username.message}</small>
            ) : null}
          </label>

          <label className="field">
            <span>密码</span>
            <input placeholder="至少 6 位" type="password" {...form.register("password")} />
            {form.formState.errors.password ? (
              <small className="field-error">{form.formState.errors.password.message}</small>
            ) : null}
          </label>

          {authMutation.error ? (
            <div className="callout callout-danger">{authMutation.error.message}</div>
          ) : null}

          <button className="primary-button full-width" type="submit" disabled={authMutation.isPending}>
            {authMutation.isPending ? "提交中..." : mode === "login" ? "进入工作台" : "创建账户"}
          </button>
        </form>
      </section>
    </main>
  );
}
