import type { ReactElement } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../../shared/auth/AuthContext";

type RequireAuthProps = {
  children: ReactElement;
};

export function RequireAuth({ children }: RequireAuthProps) {
  const location = useLocation();
  const { isBootstrapping, token } = useAuth();

  if (isBootstrapping) {
    return (
      <main className="standalone-page">
        <section className="standalone-card">
          <span className="eyebrow">Loading</span>
          <h1>正在恢复工作台</h1>
          <p>正在校验你的登录状态。</p>
        </section>
      </main>
    );
  }

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return children;
}
