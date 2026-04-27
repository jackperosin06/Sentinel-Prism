import { useAuth } from "../App";

type Props = {
  currentPath: string;
  navigate: (to: string) => void;
  reviewCount: number;
};

const NAV = [
  {
    path: "/",
    label: "Command Centre",
    icon: (
      <svg className="nav-icon" viewBox="0 0 18 18" fill="none">
        <rect x="1" y="1" width="7" height="7" rx="2" fill="currentColor" opacity="0.9" />
        <rect x="10" y="1" width="7" height="7" rx="2" fill="currentColor" opacity="0.9" />
        <rect x="1" y="10" width="7" height="7" rx="2" fill="currentColor" opacity="0.9" />
        <rect x="10" y="10" width="7" height="7" rx="2" fill="currentColor" opacity="0.9" />
      </svg>
    ),
  },
  {
    path: "/updates",
    label: "Intelligence Feed",
    icon: (
      <svg className="nav-icon" viewBox="0 0 18 18" fill="none">
        <path d="M2 4h14M2 8h10M2 12h12M2 16h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    path: "/review",
    label: "Review Queue",
    icon: (
      <svg className="nav-icon" viewBox="0 0 18 18" fill="none">
        <circle cx="9" cy="9" r="7.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M9 5v4l2.5 2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
    badge: true,
  },
  {
    path: "/briefings",
    label: "Briefings",
    icon: (
      <svg className="nav-icon" viewBox="0 0 18 18" fill="none">
        <rect x="2.5" y="1.5" width="13" height="15" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <path d="M5.5 6h7M5.5 9h7M5.5 12h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    path: "/sources",
    label: "Source Health",
    icon: (
      <svg className="nav-icon" viewBox="0 0 18 18" fill="none">
        <circle cx="9" cy="9" r="7.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M1.5 9h15M9 1.5a12 12 0 0 1 0 15M9 1.5a12 12 0 0 0 0 15" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
  {
    path: "/settings",
    label: "Settings",
    icon: (
      <svg className="nav-icon" viewBox="0 0 18 18" fill="none">
        <circle cx="9" cy="9" r="2.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M9 1.5v2M9 14.5v2M1.5 9h2M14.5 9h2M3.2 3.2l1.4 1.4M13.4 13.4l1.4 1.4M3.2 14.8l1.4-1.4M13.4 4.6l1.4-1.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
];

export function Sidebar({ currentPath, navigate, reviewCount }: Props) {
  const { me, logout } = useAuth();

  const initials = me?.email
    ? me.email.slice(0, 2).toUpperCase()
    : "SP";

  return (
    <nav className="sidebar" aria-label="Main navigation">
      <div className="sidebar-brand">
        <div className="brand-icon">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M10 2L3 5.5v5c0 4.1 3 7.9 7 9 4-1.1 7-4.9 7-9v-5L10 2z" fill="white" opacity="0.9" />
            <path d="M7 10l2 2 4-4" stroke="rgba(37,99,235,0.9)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div>
          <div className="brand-text-name">Sentinel Prism</div>
          <div className="brand-text-sub">Regulatory Intelligence</div>
        </div>
      </div>

      <div className="sidebar-nav">
        <div className="nav-section-label">Navigation</div>
        {NAV.map((item) => (
          <button
            key={item.path}
            className={`nav-link${currentPath === item.path ? " active" : ""}`}
            onClick={() => navigate(item.path)}
            type="button"
          >
            {item.icon}
            <span>{item.label}</span>
            {item.badge && reviewCount > 0 && (
              <span className="nav-badge">{reviewCount > 99 ? "99+" : reviewCount}</span>
            )}
          </button>
        ))}
      </div>

      <div className="sidebar-user">
        {me && (
          <div className="user-info">
            <div className="user-avatar">{initials}</div>
            <div style={{ minWidth: 0 }}>
              <div className="user-email truncate">{me.email}</div>
              <div className="user-role">{me.role}</div>
            </div>
          </div>
        )}
        <button className="btn-logout" type="button" onClick={logout}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M5 2H2a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h3M9 10l3-3-3-3M5 7h7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Sign out
        </button>
      </div>
    </nav>
  );
}
