import { Navigate, createBrowserRouter } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { RequireAuth } from "./shell/RequireAuth";
import { LoginPage } from "../features/auth/LoginPage";
import { JobsPage } from "../features/jobs/JobsPage";
import { ResumePage } from "../features/resume/ResumePage";

function NotFoundPage() {
  return (
    <main className="standalone-page">
      <section className="standalone-card">
        <span className="eyebrow">404</span>
        <h1>页面不存在</h1>
        <p>当前路径没有对应内容，返回工作台继续操作。</p>
        <a className="primary-button" href="/app/resume">
          返回工作台
        </a>
      </section>
    </main>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/app/resume" replace />,
  },
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/app",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      {
        index: true,
        element: <Navigate to="resume" replace />,
      },
      {
        path: "resume",
        element: <ResumePage />,
      },
      {
        path: "jobs",
        element: <JobsPage />,
      },
    ],
  },
  {
    path: "*",
    element: <NotFoundPage />,
  },
]);
