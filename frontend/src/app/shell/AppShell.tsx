import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { featureFlags } from "../../shared/config/features";

const mainNavigationItems = [
  { to: "/app/resume", label: "简历优化", helper: "围绕 JD 生成匹配摘要与优化表达" },
  ...(featureFlags.jobs
    ? [{ to: "/app/jobs", label: "求职 Copilot", helper: "对话式岗位搜索" }]
    : []),
  ...(featureFlags.interview
    ? [{ to: "/app/interview", label: "模拟面试", helper: "面试官 + 评估官" }]
    : []),
];

const profileNavigationItem = {
  to: "/app/profile",
  label: "个人中心",
  helper: "管理账号、简历、JD 和密码",
};

const SIDEBAR_COLLAPSED_KEY = "jobcopilot.sidebar.collapsed";
const SIDEBAR_MOTION_MS = 220;

export function AppShell() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") {
      return false;
    }

    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  });
  const [isSidebarAnimating, setIsSidebarAnimating] = useState(false);
  const sidebarAnimationTimerRef = useRef<number | null>(null);

  const markSidebarAnimating = () => {
    setIsSidebarAnimating(true);
    if (sidebarAnimationTimerRef.current !== null) {
      window.clearTimeout(sidebarAnimationTimerRef.current);
    }
    sidebarAnimationTimerRef.current = window.setTimeout(() => {
      setIsSidebarAnimating(false);
      sidebarAnimationTimerRef.current = null;
    }, SIDEBAR_MOTION_MS + 40);
  };

  const toggleSidebarCollapsed = (collapsed: boolean) => {
    if (isSidebarCollapsed === collapsed) {
      return;
    }
    markSidebarAnimating();
    setIsSidebarCollapsed(collapsed);
    setIsSidebarOpen(false);
  };

  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(isSidebarCollapsed));
  }, [isSidebarCollapsed]);

  useEffect(() => {
    const className = "sidebar-animating";
    if (isSidebarAnimating) {
      document.body.classList.add(className);
    } else {
      document.body.classList.remove(className);
    }

    return () => {
      document.body.classList.remove(className);
    };
  }, [isSidebarAnimating]);

  useEffect(
    () => () => {
      if (sidebarAnimationTimerRef.current !== null) {
        window.clearTimeout(sidebarAnimationTimerRef.current);
      }
      document.body.classList.remove("sidebar-animating");
    },
    [],
  );

  return (
    <div className={`shell ${isSidebarCollapsed ? "shell-collapsed" : ""}`}>
      <aside
        className={`sidebar ${isSidebarOpen ? "sidebar-open" : ""} ${isSidebarCollapsed ? "sidebar-collapsed" : ""}`}
      >
        <div className="sidebar-brand">
          <button
            aria-label={isSidebarCollapsed ? "展开侧边栏" : "侧边栏 Logo"}
            className={`sidebar-brand-mark ${isSidebarCollapsed ? "sidebar-brand-toggle" : ""}`}
            title={isSidebarCollapsed ? "展开侧边栏" : "Job Copilot"}
            type="button"
            onClick={() => {
              if (isSidebarCollapsed) {
                toggleSidebarCollapsed(false);
              }
            }}
          >
            JC
          </button>
          <div className="sidebar-brand-copy">
            <p className="sidebar-kicker">求职工作台</p>
            <h1>Job Copilot</h1>
          </div>
          {!isSidebarCollapsed ? (
            <button
              aria-label="收起侧边栏"
              className="sidebar-collapse-button"
              title="收起侧边栏"
              type="button"
              onClick={() => toggleSidebarCollapsed(true)}
            >
              {"<"}
            </button>
          ) : null}
        </div>

        <nav className="sidebar-nav">
          {mainNavigationItems.map((item) => (
            <NavLink
              key={item.to}
              className={({ isActive }) => `sidebar-link ${isActive ? "sidebar-link-active" : ""}`}
              title={item.label}
              to={item.to}
              onClick={() => setIsSidebarOpen(false)}
            >
              <span className="sidebar-link-copy">
                <span>{item.label}</span>
                <small>{item.helper}</small>
              </span>
            </NavLink>
          ))}
        </nav>

        <nav className="sidebar-nav sidebar-nav-bottom">
          <NavLink
            className={({ isActive }) => `sidebar-link ${isActive ? "sidebar-link-active" : ""}`}
            title={profileNavigationItem.label}
            to={profileNavigationItem.to}
            onClick={() => setIsSidebarOpen(false)}
          >
            <span className="sidebar-link-copy">
              <span>{profileNavigationItem.label}</span>
              <small>{profileNavigationItem.helper}</small>
            </span>
          </NavLink>
        </nav>
      </aside>

      <div
        className={`sidebar-overlay ${isSidebarOpen ? "sidebar-overlay-visible" : ""}`}
        onClick={() => setIsSidebarOpen(false)}
      />

      <main className="shell-main">
        <button className="mobile-sidebar-toggle" type="button" onClick={() => setIsSidebarOpen(true)}>
          <span />
          <span />
          <span />
        </button>

        <div className="content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
