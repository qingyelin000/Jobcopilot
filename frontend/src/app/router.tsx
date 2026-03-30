import { Navigate, createBrowserRouter } from "react-router-dom";
import { LoginPage } from "../features/auth/LoginPage";
import { InterviewPage } from "../features/interview/InterviewPage";
import { JobsPage } from "../features/jobs/JobsPage";
import { ProfilePage } from "../features/profile/ProfilePage";
import { ResumePage } from "../features/resume/ResumePage";
import { featureFlags } from "../shared/config/features";
import { AppShell } from "./shell/AppShell";
import { RequireAuth } from "./shell/RequireAuth";

function NotFoundPage() {
  return (
    <main className="standalone-page">
      <section className="standalone-card">
        <span className="eyebrow">404</span>
        <h1>页面不存在</h1>
        <p>当前路径没有对应内容，请返回简历优化页面继续操作。</p>
        <a className="primary-button" href="/app/resume">
          返回简历优化
        </a>
      </section>
    </main>
  );
}

const appChildren = [
  {
    index: true,
    element: <Navigate to="resume" replace />,
  },
  {
    path: "resume",
    element: <ResumePage />,
  },
  {
    path: "assets",
    element: <Navigate to="/app/profile" replace />,
  },
  {
    path: "profile",
    element: <ProfilePage />,
  },
];

if (featureFlags.jobs) {
  appChildren.push({
    path: "jobs",
    element: <JobsPage />,
  });
}

if (featureFlags.interview) {
  appChildren.push({
    path: "interview",
    element: <InterviewPage />,
  });
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
    children: appChildren,
  },
  {
    path: "*",
    element: <NotFoundPage />,
  },
]);
