import { type FormEvent, useCallback, useEffect, useState } from "react";

import { Dashboard } from "./components/Dashboard";
import { readErrorMessage } from "./httpErrors";

const TOKEN_KEY = "sentinel_prism_token";
const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

// Strings we must treat as "no token" even though they are technically
// non-null — these come from prior bad writes (e.g. ``JSON.stringify(null)``)
// and would otherwise stick the UI in ``Authorization: Bearer null``.
const INVALID_TOKEN_VALUES = new Set(["", "null", "undefined"]);

function readStoredToken(): string {
  const raw = localStorage.getItem(TOKEN_KEY);
  if (raw === null) return "";
  return INVALID_TOKEN_VALUES.has(raw) ? "" : raw;
}

type NotificationItem = {
  id: string;
  run_id: string;
  item_url: string;
  team_slug: string;
  severity: string;
  title: string;
  body: string | null;
  read_at: string | null;
  created_at: string;
};

type NotificationListResponse = {
  items: NotificationItem[];
  has_more: boolean;
};

export default function App() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState<string>(() => readStoredToken());
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken("");
    setItems([]);
  }, []);

  async function login(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const r = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!r.ok) {
        setErr(await readErrorMessage(r));
        return;
      }
      const j = (await r.json()) as { access_token: string };
      localStorage.setItem(TOKEN_KEY, j.access_token);
      setToken(j.access_token);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Network error during login.");
    }
  }

  useEffect(() => {
    if (!token) return;
    const ctrl = new AbortController();
    (async () => {
      setErr(null);
      try {
        const r = await fetch(`${API_BASE}/notifications`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (r.status === 401) {
          // Token expired or revoked — clear it so the user sees the login
          // form instead of an empty list with a cryptic error blob.
          setErr("Session expired. Please log in again.");
          logout();
          return;
        }
        if (!r.ok) {
          setErr(await readErrorMessage(r));
          return;
        }
        const j = (await r.json()) as NotificationListResponse;
        setItems(j.items);
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        setErr(
          e instanceof Error ? e.message : "Network error loading notifications."
        );
      }
    })();
    return () => ctrl.abort();
  }, [token, logout]);

  async function markRead(id: string) {
    try {
      const r = await fetch(`${API_BASE}/notifications/${id}/read`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 204) {
        const ts = new Date().toISOString();
        setItems((xs) => xs.map((i) => (i.id === id ? { ...i, read_at: ts } : i)));
        return;
      }
      if (r.status === 401) {
        setErr("Session expired. Please log in again.");
        logout();
        return;
      }
      setErr(await readErrorMessage(r));
    } catch (e) {
      setErr(
        e instanceof Error ? e.message : "Network error marking notification read."
      );
    }
  }

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 960, margin: "2rem auto", padding: 16 }}>
      <h1>Sentinel Prism</h1>
      {!token ? (
        <form onSubmit={login}>
          <p>Log in to open the analyst console (dashboard and notifications).</p>
          <p style={{ fontSize: "0.9rem", color: "#444" }}>
            API base: {API_BASE} (set <code>VITE_API_URL</code> if needed).
          </p>
          <div style={{ marginBottom: 8 }}>
            <label>
              Email{" "}
              <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required />
            </label>
          </div>
          <div style={{ marginBottom: 8 }}>
            <label>
              Password{" "}
              <input
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                type="password"
                required
              />
            </label>
          </div>
          <button type="submit">Login</button>
        </form>
      ) : (
        <>
          <p>
            <button type="button" onClick={logout}>
              Log out
            </button>
          </p>
          <Dashboard apiBase={API_BASE} token={token} />
          <h2 style={{ marginTop: "2rem" }}>Notifications</h2>
          <p style={{ fontSize: "0.9rem", color: "#444" }}>
            In-app inbox (Story 5.2); critical routed items for your team.
          </p>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {items.map((n) => (
              <li
                key={n.id}
                style={{
                  borderBottom: "1px solid #ccc",
                  padding: "8px 0",
                  opacity: n.read_at ? 0.6 : 1,
                }}
              >
                <strong>{n.title}</strong>{" "}
                <span style={{ fontSize: "0.8rem", color: "#666" }}>
                  [{n.severity}] {n.team_slug}
                </span>{" "}
                {n.read_at ? "(read)" : "(unread)"}
                <br />
                <button type="button" onClick={() => markRead(n.id)} disabled={!!n.read_at}>
                  Mark read
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {err && <pre style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{err}</pre>}
    </main>
  );
}
