import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Login } from "./pages/Login";
import { Sidebar } from "./components/Sidebar";
import { CommandCentre } from "./pages/CommandCentre";
import { Updates } from "./pages/Updates";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Briefings } from "./pages/Briefings";
import { Sources } from "./pages/Sources";
import { Settings } from "./pages/Settings";

export const TOKEN_KEY = "sentinel_prism_token";
export const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const INVALID = new Set(["", "null", "undefined"]);

export function readToken(): string {
  const raw = localStorage.getItem(TOKEN_KEY) ?? "";
  return INVALID.has(raw) ? "" : raw;
}

export type MeResponse = {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
};

export type AuthCtx = {
  token: string;
  me: MeResponse | null;
  logout: () => void;
  apiBase: string;
};

export const AuthContext = createContext<AuthCtx>(null!);
export const useAuth = () => useContext(AuthContext);

export async function readErrorMessage(r: Response): Promise<string> {
  try {
    const ct = r.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) {
      const j = (await r.json()) as { detail?: string | { msg?: string }[] };
      if (typeof j.detail === "string") return j.detail;
      if (Array.isArray(j.detail) && j.detail[0]?.msg) return j.detail[0].msg;
    }
  } catch { /* fall through */ }
  return `Request failed (${r.status} ${r.statusText})`;
}

function useHashPath(): [string, (to: string) => void] {
  const [path, setPath] = useState(() => window.location.hash.replace(/^#/, "") || "/");
  useEffect(() => {
    const handler = () => setPath(window.location.hash.replace(/^#/, "") || "/");
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);
  const navigate = (to: string) => { window.location.hash = to; };
  return [path, navigate];
}

export default function App() {
  const [token, setToken] = useState<string>(() => readToken());
  const [me, setMe] = useState<MeResponse | null>(null);
  const [reviewCount, setReviewCount] = useState(0);
  const [path, navigate] = useHashPath();

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken("");
    setMe(null);
    setReviewCount(0);
  }, []);

  function handleLogin(accessToken: string) {
    localStorage.setItem(TOKEN_KEY, accessToken);
    setToken(accessToken);
  }

  useEffect(() => {
    if (!token) { setMe(null); return; }
    const ctrl = new AbortController();
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (r.status === 401) { logout(); return; }
        if (r.ok) setMe((await r.json()) as MeResponse);
      } catch { /* ignore */ }
    })();
    return () => ctrl.abort();
  }, [token, logout]);

  useEffect(() => {
    if (!token) { setReviewCount(0); return; }
    const ctrl = new AbortController();
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/dashboard/summary`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (r.ok) {
          const j = (await r.json()) as { review_queue_count?: number };
          setReviewCount(j.review_queue_count ?? 0);
        }
      } catch { /* ignore */ }
    };
    void load();
    const id = setInterval(() => void load(), 30_000);
    return () => { ctrl.abort(); clearInterval(id); };
  }, [token]);

  if (!token) {
    return <Login onLogin={handleLogin} apiBase={API_BASE} />;
  }

  function renderPage() {
    switch (path) {
      case "/": return <CommandCentre />;
      case "/updates": return <Updates />;
      case "/review": return <ReviewQueue />;
      case "/briefings": return <Briefings />;
      case "/sources": return <Sources />;
      case "/settings": return <Settings />;
      default: return <CommandCentre />;
    }
  }

  return (
    <AuthContext.Provider value={{ token, me, logout, apiBase: API_BASE }}>
      <div className="app-shell">
        <Sidebar currentPath={path} navigate={navigate} reviewCount={reviewCount} />
        <main className="app-main">
          {renderPage()}
        </main>
      </div>
    </AuthContext.Provider>
  );
}
