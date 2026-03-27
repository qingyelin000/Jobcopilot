import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { featureFlags } from "../../shared/config/features";

const mainNavigationItems = [
  { to: "/app/resume", label: "简历优化", helper: "围绕 JD 生成匹配摘要与优化表达", icon: "CV" },
  ...(featureFlags.jobs
    ? [{ to: "/app/jobs", label: "求职 Copilot", helper: "对话式岗位搜索", icon: "AI" }]
    : []),
];
const profileNavigationItem = { to: "/app/profile", label: "个人信息", helper: "管理账号、简历、JD 和密码", icon: "ME" };

const SIDEBAR_COLLAPSED_KEY = "jobcopilot.sidebar.collapsed";

export function AppShell() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") {
      return false;
    }

    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  });

  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(isSidebarCollapsed));
  }, [isSidebarCollapsed]);

  return (
    <div className={`shell ${isSidebarCollapsed ? "shell-collapsed" : ""}`}>
      <aside
        className={`sidebar ${isSidebarOpen ? "sidebar-open" : ""} ${isSidebarCollapsed ? "sidebar-collapsed" : ""}`}
      >
        <div className="sidebar-brand">
          <div className="sidebar-brand-mark">JC</div>
          <div className="sidebar-brand-copy">
            <p className="sidebar-kicker">Workstation</p>
            <h1>JobCopilot</h1>
          </div>
          <button
            aria-label={isSidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
            className="sidebar-collapse-button"
            title={isSidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
            type="button"
            onClick={() => setIsSidebarCollapsed((current) => !current)}
          >
            {isSidebarCollapsed ? ">" : "<"}
          </button>
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
              <span className="sidebar-link-icon">{item.icon}</span>
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
            <span className="sidebar-link-icon">{profileNavigationItem.icon}</span>
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
