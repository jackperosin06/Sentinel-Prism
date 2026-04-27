import { type FormEvent, useCallback, useEffect, useId, useState } from "react";

import { readErrorMessage } from "../httpErrors";

type Active = {
  version: number;
  label_policy_text: string;
  refresh_cadence: "quarterly";
  refresh_after_major_classification_change: boolean;
};

type Draft = {
  label_policy_text: string;
  refresh_cadence: "quarterly";
  refresh_after_major_classification_change: boolean;
  reason: string | null;
};

type StateResponse = {
  active: Active;
  draft: Draft | null;
};

type HistoryItem = {
  id: string;
  created_at: string;
  actor_user_id: string | null;
  actor_email: string | null;
  prior_version: number;
  new_version: number;
  prior_refresh_cadence: string;
  new_refresh_cadence: string;
  prior_refresh_after_major_classification_change: boolean;
  new_refresh_after_major_classification_change: boolean;
  reason: string | null;
};

type HistoryResponse = {
  items: HistoryItem[];
};

type Props = {
  apiBase: string;
  token: string;
  onUnauthorized: () => void;
};

export function GoldenSetPolicyAdmin({ apiBase, token, onUnauthorized }: Props) {
  const sectionId = useId();
  const [state, setState] = useState<StateResponse | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [draftPolicy, setDraftPolicy] = useState("");
  const [draftCadence, setDraftCadence] = useState<"quarterly">("quarterly");
  const [draftPostMajor, setDraftPostMajor] = useState(true);
  const [draftReason, setDraftReason] = useState("");
  const [applyReason, setApplyReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      const [stateR, histR] = await Promise.all([
        fetch(`${apiBase}/admin/golden-set-policy`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
        fetch(`${apiBase}/admin/golden-set-policy/history`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
      ]);
      if (stateR.status === 401) {
        setState(null);
        setErr("Session expired. Log in again.");
        onUnauthorized();
        return;
      }
      if (stateR.status === 403) {
        setState(null);
        setErr("Golden-set policy is available to admin accounts only.");
        return;
      }
      if (!stateR.ok) {
        setState(null);
        setErr(await readErrorMessage(stateR));
        return;
      }
      if (histR.status === 401) {
        onUnauthorized();
        return;
      }
      if (histR.status === 403) {
        setHistory([]);
      } else if (histR.ok) {
        const h = (await histR.json()) as HistoryResponse;
        setHistory(h.items);
      } else {
        setErr(await readErrorMessage(histR));
        setHistory([]);
        return;
      }
      const j = (await stateR.json()) as StateResponse;
      setState(j);
      const d = j.draft ?? j.active;
      setDraftPolicy(d.label_policy_text);
      setDraftCadence(d.refresh_cadence);
      setDraftPostMajor(d.refresh_after_major_classification_change);
      setDraftReason(j.draft?.reason ?? "");
    } catch (e) {
      setState(null);
      setErr(e instanceof Error ? e.message : "Failed to load golden-set policy.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, onUnauthorized, token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onSaveDraft(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setSaving(true);
    try {
      const r = await fetch(`${apiBase}/admin/golden-set-policy/draft`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          label_policy_text: draftPolicy,
          refresh_cadence: draftCadence,
          refresh_after_major_classification_change: draftPostMajor,
          reason: draftReason.trim() || null,
        }),
      });
      if (r.status === 401) {
        onUnauthorized();
        return;
      }
      if (!r.ok) {
        setErr(await readErrorMessage(r));
        return;
      }
      const j = (await r.json()) as StateResponse;
      setState(j);
      const histR = await fetch(`${apiBase}/admin/golden-set-policy/history`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (histR.ok) {
        const h = (await histR.json()) as HistoryResponse;
        setHistory(h.items);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to save draft.");
    } finally {
      setSaving(false);
    }
  }

  async function onApply() {
    setErr(null);
    const ok = window.confirm(
      "Apply the saved draft? This becomes the active golden-set policy, increments the version, and is recorded in the audit log."
    );
    if (!ok) return;
    setApplying(true);
    try {
      const r = await fetch(`${apiBase}/admin/golden-set-policy/apply`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          reason: applyReason.trim() || null,
        }),
      });
      if (r.status === 401) {
        onUnauthorized();
        return;
      }
      if (r.status === 400) {
        const j = (await r.json()) as { detail?: string };
        setErr(j.detail ?? "Cannot apply: save a draft first.");
        return;
      }
      if (!r.ok) {
        setErr(await readErrorMessage(r));
        return;
      }
      setApplyReason("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to apply policy.");
    } finally {
      setApplying(false);
    }
  }

  return (
    <section
      id={sectionId}
      style={{
        marginTop: "2rem",
        padding: 16,
        background: "#f8f9fb",
        borderRadius: 8,
        border: "1px solid #dde1e7",
      }}
      aria-labelledby={`${sectionId}-title`}
    >
      <h2 id={`${sectionId}-title`} style={{ marginTop: 0 }}>
        Golden set / evaluation policy
      </h2>
      <p style={{ fontSize: "0.9rem", color: "#444", maxWidth: "70ch" }}>
        Regulatory / policy owner defines reference label rules and eval refresh discipline (FR44,
        FR45). <strong>Active</strong> policy is used for governance; <strong>draft</strong> is
        staged until you apply.
      </p>
      {loading ? (
        <p aria-live="polite">Loading…</p>
      ) : state ? (
        <>
          <div
            style={{
              marginBottom: 16,
              padding: 12,
              background: "#fff",
              border: "1px solid #ccc",
              borderRadius: 6,
            }}
          >
            <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Active</h3>
            <p style={{ margin: "0.25rem 0", fontSize: "0.9rem" }}>
              Version <strong>{state.active.version}</strong> — cadence{" "}
              <strong>{state.active.refresh_cadence}</strong> — refresh after major classification
              change: <strong>{state.active.refresh_after_major_classification_change ? "yes" : "no"}</strong>
            </p>
            <pre
              style={{
                marginTop: 8,
                padding: 8,
                background: "#f0f0f0",
                fontSize: "0.75rem",
                maxHeight: 160,
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {state.active.label_policy_text}
            </pre>
          </div>
          <form onSubmit={onSaveDraft}>
            <h3 style={{ fontSize: "1rem" }}>Draft</h3>
            <div style={{ marginBottom: 8 }}>
              <label style={{ display: "block", marginBottom: 4 }}>Label policy text</label>
              <textarea
                value={draftPolicy}
                onChange={(e) => setDraftPolicy(e.target.value)}
                required
                rows={10}
                style={{ width: "100%", maxWidth: 720, fontFamily: "monospace", fontSize: "0.85rem" }}
              />
            </div>
            <div style={{ marginBottom: 8 }}>
              <label>
                Refresh cadence{" "}
                <select
                  value={draftCadence}
                  onChange={(e) => setDraftCadence(e.target.value as "quarterly")}
                >
                  <option value="quarterly">Quarterly</option>
                </select>
              </label>
            </div>
            <div style={{ marginBottom: 8 }}>
              <label>
                <input
                  type="checkbox"
                  checked={draftPostMajor}
                  onChange={(e) => setDraftPostMajor(e.target.checked)}
                />{" "}
                Also plan refresh after major model or prompt change (classification)
              </label>
            </div>
            <div style={{ marginBottom: 8 }}>
              <label style={{ display: "block", marginBottom: 4 }}>Draft note (optional)</label>
              <input
                type="text"
                value={draftReason}
                onChange={(e) => setDraftReason(e.target.value)}
                style={{ width: "100%", maxWidth: 720 }}
              />
            </div>
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save draft"}
            </button>
          </form>
          <div style={{ marginTop: 16 }}>
            <label style={{ display: "block", marginBottom: 4 }}>
              Apply note (optional, audit metadata)
            </label>
            <input
              type="text"
              value={applyReason}
              onChange={(e) => setApplyReason(e.target.value)}
              style={{ width: "100%", maxWidth: 720, marginBottom: 8 }}
            />
            <button type="button" onClick={() => void onApply()} disabled={applying}>
              {applying ? "Applying…" : "Apply draft"}
            </button>
          </div>
          <div style={{ marginTop: 24 }}>
            <h3 style={{ fontSize: "1rem" }}>Configuration history</h3>
            {history.length === 0 ? (
              <p style={{ fontSize: "0.9rem", color: "#555" }}>No apply events yet.</p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                  <thead>
                    <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
                      <th style={{ padding: 8 }}>When (UTC)</th>
                      <th style={{ padding: 8 }}>Actor</th>
                      <th style={{ padding: 8 }}>Version</th>
                      <th style={{ padding: 8 }}>Cadence / flag</th>
                      <th style={{ padding: 8 }}>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h) => (
                      <tr key={h.id} style={{ borderBottom: "1px solid #eee" }}>
                        <td style={{ padding: 8, whiteSpace: "nowrap" }}>{h.created_at}</td>
                        <td style={{ padding: 8 }}>{h.actor_email ?? h.actor_user_id ?? "—"}</td>
                        <td style={{ padding: 8 }}>
                          {h.prior_version} → {h.new_version}
                        </td>
                        <td style={{ padding: 8 }}>
                          {h.prior_refresh_cadence} → {h.new_refresh_cadence}
                          <br />
                          post-major: {String(h.prior_refresh_after_major_classification_change)} →{" "}
                          {String(h.new_refresh_after_major_classification_change)}
                        </td>
                        <td style={{ padding: 8 }}>{h.reason ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : null}
      {err ? (
        <p style={{ color: "crimson", marginTop: 12, whiteSpace: "pre-wrap" }} role="alert">
          {err}
        </p>
      ) : null}
    </section>
  );
}
