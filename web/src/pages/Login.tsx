import { type FormEvent, useState } from "react";
import { readErrorMessage } from "../App";

type Props = {
  onLogin: (token: string) => void;
  apiBase: string;
};

export function Login({ onLogin, apiBase }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const r = await fetch(`${apiBase}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (!r.ok) {
        setError(await readErrorMessage(r));
        return;
      }
      const j = (await r.json()) as { access_token: string };
      onLogin(j.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to connect to the server.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-bg">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-brand-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path d="M12 2L4 6.5v6c0 5 3.6 9.6 8 11 4.4-1.4 8-6 8-11v-6L12 2z" fill="white" opacity="0.95" />
              <path d="M8.5 12l3 3 5-5" stroke="rgba(37,99,235,0.95)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div>
            <div className="login-title">Sentinel Prism</div>
            <div className="login-subtitle-brand">Regulatory Intelligence Platform</div>
          </div>
        </div>

        <div className="login-form-heading">Welcome back</div>
        <div className="login-form-desc">Sign in to access your compliance dashboard.</div>

        {error && (
          <div className="error-banner" role="alert">
            <svg width="15" height="15" viewBox="0 0 15 15" fill="none" style={{ flexShrink: 0, marginTop: 1 }}>
              <circle cx="7.5" cy="7.5" r="6.5" stroke="currentColor" strokeWidth="1.5" />
              <path d="M7.5 4.5v3M7.5 10h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <div className="form-group" style={{ marginBottom: 14 }}>
            <label className="form-label" htmlFor="login-email">Email address</label>
            <input
              id="login-email"
              className="form-input"
              type="email"
              autoComplete="email"
              placeholder="you@pharma.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={loading}
            />
          </div>

          <div className="form-group" style={{ marginBottom: 22 }}>
            <label className="form-label" htmlFor="login-password">Password</label>
            <input
              id="login-password"
              className="form-input"
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={loading}
            />
          </div>

          <button
            type="submit"
            className="btn btn-primary login-submit"
            disabled={loading || !email.trim() || !password}
          >
            {loading ? (
              <>
                <div className="spinner" style={{ width: 16, height: 16 }} />
                Signing in…
              </>
            ) : "Sign in"}
          </button>
        </form>

        <p style={{ marginTop: 24, fontSize: 12, color: "var(--text-dim)", textAlign: "center" }}>
          Monitoring FDA · EMA · TGA regulatory updates
        </p>
      </div>
    </div>
  );
}
