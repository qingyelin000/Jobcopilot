import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../shared/auth/AuthContext";

type AuthMode = "login" | "register";

type AuthFormValues = {
  username: string;
  password: string;
  confirmPassword: string;
};

export function LoginPage() {
  const [mode, setMode] = useState<AuthMode>("login");
  const { authenticate, token } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const redirectTarget = useMemo(() => {
    const state = location.state as { from?: string } | null;
    return state?.from || "/app/resume";
  }, [location.state]);

  const form = useForm<AuthFormValues>({
    defaultValues: {
      username: "",
      password: "",
      confirmPassword: "",
    },
  });

  const authMutation = useMutation<void, Error, { username: string; password: string }>({
    mutationFn: async (values) => {
      await authenticate(mode, values);
    },
    onSuccess: () => {
      navigate(redirectTarget, { replace: true });
    },
  });

  if (token) {
    return <Navigate to={redirectTarget} replace />;
  }

  const handleModeChange = (nextMode: AuthMode) => {
    setMode(nextMode);
    authMutation.reset();
    form.clearErrors();
    form.setValue("password", "");
    form.setValue("confirmPassword", "");
  };

  const handleSubmit = form.handleSubmit((values) => {
    const username = values.username.trim();

    if (mode === "register" && values.password !== values.confirmPassword) {
      form.setError("confirmPassword", {
        type: "validate",
        message: "两次输入的密码不一致",
      });
      return;
    }

    form.clearErrors("confirmPassword");
    authMutation.mutate({
      username,
      password: values.password,
    });
  });

  return (
    <main className="auth-simple-page">
      <section className="auth-simple-card">
        <h1 className="auth-simple-brand">JobCopilot</h1>

        <div className="auth-mode-switch auth-simple-switch">
          <button
            className={mode === "login" ? "primary-button" : "secondary-button"}
            type="button"
            onClick={() => handleModeChange("login")}
          >
            登录
          </button>
          <button
            className={mode === "register" ? "primary-button" : "secondary-button"}
            type="button"
            onClick={() => handleModeChange("register")}
          >
            注册
          </button>
        </div>

        <form className="form-stack" onSubmit={handleSubmit} noValidate>
          <label className="field">
            <span>用户名</span>
            <input
              autoComplete="username"
              placeholder="请输入用户名"
              {...form.register("username", {
                required: "请输入用户名",
                minLength: {
                  value: 3,
                  message: "用户名至少 3 个字符",
                },
              })}
            />
            {form.formState.errors.username ? (
              <small className="field-error">{form.formState.errors.username.message}</small>
            ) : null}
          </label>

          <label className="field">
            <span>密码</span>
            <input
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="请输入密码"
              type="password"
              {...form.register("password", {
                required: "请输入密码",
                minLength: {
                  value: 6,
                  message: "密码至少 6 个字符",
                },
              })}
            />
            {form.formState.errors.password ? (
              <small className="field-error">{form.formState.errors.password.message}</small>
            ) : null}
          </label>

          {mode === "register" ? (
            <label className="field">
              <span>确认密码</span>
              <input
                autoComplete="new-password"
                placeholder="请再次输入密码"
                type="password"
                {...form.register("confirmPassword", {
                  required: "请再次输入密码",
                })}
              />
              {form.formState.errors.confirmPassword ? (
                <small className="field-error">{form.formState.errors.confirmPassword.message}</small>
              ) : null}
            </label>
          ) : null}

          {authMutation.error ? <div className="callout callout-danger">{authMutation.error.message}</div> : null}

          <button className="primary-button full-width" type="submit" disabled={authMutation.isPending}>
            {authMutation.isPending ? "提交中..." : mode === "login" ? "登录" : "注册"}
          </button>
        </form>
      </section>
    </main>
  );
}
