import { type FormEvent, useCallback, useEffect, useId, useState } from "react";

import { readErrorMessage } from "../httpErrors";

type Active = {
  version: number;
  low_confidence_threshold: number;
  system_prompt: string;
};

type Draft = {
  low_confidence_threshold: number;
  system_prompt: string;
  reason: string | null;
};

type StateResponse = {
  active: Active;
  draft: Draft | null;
};

type Props = {
  apiBase: string;
  token: string;
  onUnauthorized: () => void;
};

export function ClassificationPolicyAdmin({ apiBase, token, onUnauthorized }: Props) {
  const sectionId = useId();
  const [state, setState] = useState<StateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [draftThreshold, setDraftThreshold] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftReason, setDraftReason] = useState("");
  const [applyReason, setApplyReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      const r = await fetch(`${apiBase}/admin/classification-policy`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 401) {
        setState(null);
        setErr("Session expired. Log in again.");
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        setState(null);
        setErr("Classification policy is available to admin accounts only.");
        return;
      }
      if (!r.ok) {
        setState(null);
        setErr(await readErrorMessage(r));
        return;
      }
      const j = (await r.json()) as StateResponse;
      setState(j);
      const d = j.draft ?? j.active;
      setDraftThreshold(String(d.low_confidence_threshold));
      setDraftPrompt(d.system_prompt);
      setDraftReason(j.draft?.reason ?? "");
    } catch (e) {
      setState(null);
      setErr(e instanceof Error ? e.message : "Failed to load classification policy.");
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
    const th = Number(draftThreshold);
    try {
      const r = await fetch(`${apiBase}/admin/classification-policy/draft`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          low_confidence_threshold: th,
          system_prompt: draftPrompt,
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
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to save draft.");
    } finally {
      setSaving(false);
    }
  }

  async function onApply() {
    setErr(null);
    const ok = window.confirm(
      "Apply the saved draft to production? This increments the policy version and is recorded in the audit log."
    );
    if (!ok) return;
    setApplying(true);
    try {
      const r = await fetch(`${apiBase}/admin/classification-policy/apply`, {
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
        Classification policy
      </h2>
      <p style={{ fontSize: "0.9rem", color: "#444", maxWidth: "70ch" }}>
        Draft threshold and system prompt changes here. Production classification uses the{" "}
        <strong>active</strong> version until you <strong>apply</strong> a draft (governed change,
        FR29).
      </p>
      {loading ? (
        <p aria-live="polite">Loading policy…</p>
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
            <h3 style={{ marginTop: 0, fontSize: "1rem" }}>Active (production)</h3>
            <p style={{ margin: "0.25rem 0", fontSize: "0.9rem" }}>
              Version <strong>{state.active.version}</strong> — threshold{" "}
              <strong>{state.active.low_confidence_threshold}</strong>
            </p>
            <pre
              style={{
                marginTop: 8,
                padding: 8,
                background: "#f0f0f0",
                fontSize: "0.75rem",
                maxHeight: 120,
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {state.active.system_prompt}
            </pre>
          </div>
          <form onSubmit={onSaveDraft}>
            <h3 style={{ fontSize: "1rem" }}>Draft</h3>
            <div style={{ marginBottom: 8 }}>
              <label>
                Low confidence threshold (0–1){" "}
                <input
                  type="number"
                  step="0.01"
                  min={0}
                  max={1}
                  value={draftThreshold}
                  onChange={(e) => setDraftThreshold(e.target.value)}
                  required
                  style={{ width: 120 }}
                />
              </label>
            </div>
            <div style={{ marginBottom: 8 }}>
              <label style={{ display: "block", marginBottom: 4 }}>System prompt</label>
              <textarea
                value={draftPrompt}
                onChange={(e) => setDraftPrompt(e.target.value)}
                required
                rows={8}
                style={{ width: "100%", maxWidth: 720, fontFamily: "monospace", fontSize: "0.85rem" }}
              />
            </div>
            <div style={{ marginBottom: 8 }}>
              <label style={{ display: "block", marginBottom: 4 }}>Draft note (optional)</label>
              <input
                type="text"
                value={draftReason}
                onChange={(e) => setDraftReason(e.target.value)}
                style={{ width: "100%", maxWidth: 720 }}
                placeholder="Why this change is proposed"
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
              {applying ? "Applying…" : "Apply draft to production"}
            </button>
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
