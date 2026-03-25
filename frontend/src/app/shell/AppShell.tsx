import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../shared/auth/AuthContext";

const navigationItems = [
  { to: "/app/resume", label: "简历分析师", helper: "JD 匹配与简历优化", icon: "CV" },
  { to: "/app/jobs", label: "求职 Copilot", helper: "对话式岗位搜索", icon: "AI" },
];

const SIDEBAR_COLLAPSED_KEY = "jobcopilot.sidebar.collapsed";

function routeMeta(pathname: string) {
  if (pathname.includes("/jobs")) {
    return {
      title: "求职 Copilot",
      subtitle: "像聊天一样搜索岗位，并围绕城市、方向和偏好持续收敛结果。",
    };
  }

  return {
    title: "简历分析师",
    subtitle: "围绕目标 JD 生成更匹配的简历内容。",
  };
}

export function AppShell() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") {
      return false;
    }

    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  });

  const location = useLocation();
  const navigate = useNavigate();
  const { logout, user } = useAuth();
  const meta = useMemo(() => routeMeta(location.pathname), [location.pathname]);
  const isResumePage = location.pathname.includes("/resume");
  const showTopbar = !isResumePage;

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
            className="sidebar-collapse-button"
            type="button"
            onClick={() => setIsSidebarCollapsed((current) => !current)}
            aria-label={isSidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
            title={isSidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
          >
            {isSidebarCollapsed ? ">" : "<"}
          </button>
        </div>

        <nav className="sidebar-nav">
          {navigationItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              title={item.label}
              className={({ isActive }) => `sidebar-link ${isActive ? "sidebar-link-active" : ""}`}
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

        <section className="sidebar-panel">
          <div className="sidebar-panel-copy">
            <span className="sidebar-kicker">Session</span>
            <strong>{user?.username ?? "未登录"}</strong>
            <p>从这里进入简历优化和岗位搜索。</p>
          </div>
        </section>

        <button className="ghost-button sidebar-logout" onClick={logout} type="button">
          <span className="sidebar-logout-copy">退出登录</span>
        </button>
      </aside>

      <div
        className={`sidebar-overlay ${isSidebarOpen ? "sidebar-overlay-visible" : ""}`}
        onClick={() => setIsSidebarOpen(false)}
      />

      <main className="shell-main">
        {showTopbar ? (
          <header className="topbar">
            <div className="topbar-title">
              <button className="ghost-icon-button" type="button" onClick={() => setIsSidebarOpen((current) => !current)}>
                <span />
                <span />
                <span />
              </button>
              <div>
                <span className="eyebrow">JobCopilot Web</span>
                <h2>{meta.title}</h2>
              </div>
            </div>

            <div className="topbar-actions">
              <span className="topbar-subtitle">{meta.subtitle}</span>
              <button className="secondary-button" type="button" onClick={() => navigate("/app/jobs")}>
                新对话
              </button>
            </div>
          </header>
        ) : (
          <button className="mobile-sidebar-toggle" type="button" onClick={() => setIsSidebarOpen(true)}>
            <span />
            <span />
            <span />
          </button>
        )}

        <div className="content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
