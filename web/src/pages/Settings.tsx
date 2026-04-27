import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useAuth, readErrorMessage } from "../App";
import { formatDate } from "../utils";

type Tab = "routing" | "policy" | "audit";

type Rule = {
  id: string;
  priority: number;
  rule_type: "topic" | "severity";
  impact_category: string | null;
  severity_value: string | null;
  team_slug: string;
  channel_slug: string;
  created_at: string;
};

type PolicyState = {
  active: { version: number; low_confidence_threshold: number; system_prompt: string };
  draft: { low_confidence_threshold: number; system_prompt: string; reason: string | null } | null;
};

type AuditItem = {
  id: string;
  created_at: string;
  run_id: string;
  action: string;
  source_id: string | null;
  actor_user_id: string | null;
  metadata: Record<string, unknown> | null;
};

const ACTION_LABELS: Record<string, string> = {
  pipeline_scout_completed: "Scout completed",
  pipeline_normalize_completed: "Normalizer completed",
  pipeline_classify_completed: "Classified",
  human_review_approved: "Review approved",
  human_review_rejected: "Review rejected",
  human_review_overridden: "Review overridden",
  briefing_generated: "Briefing generated",
  routing_applied: "Routing applied",
  routing_config_changed: "Routing config changed",
  classification_config_changed: "Classification config changed",
};

export function Settings() {
  const [tab, setTab] = useState<Tab>("routing");

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Settings</div>
          <div className="page-subtitle">
            Manage routing rules, classification policy, and audit log.
          </div>
        </div>
      </div>

      <div className="tab-bar">
        <button className={`tab-btn${tab === "routing" ? " active" : ""}`} type="button" onClick={() => setTab("routing")}>
          Routing Rules
        </button>
        <button className={`tab-btn${tab === "policy" ? " active" : ""}`} type="button" onClick={() => setTab("policy")}>
          Classification Policy
        </button>
        <button className={`tab-btn${tab === "audit" ? " active" : ""}`} type="button" onClick={() => setTab("audit")}>
          Audit Log
        </button>
      </div>

      {tab === "routing" && <RoutingTab />}
      {tab === "policy" && <PolicyTab />}
      {tab === "audit" && <AuditTab />}
    </div>
  );
}

/* ============================================================
   ROUTING RULES TAB
   ============================================================ */

function RoutingTab() {
  const { token, apiBase, me } = useAuth();
  const isAdmin = me?.role === "admin";

  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [addType, setAddType] = useState<"topic" | "severity">("topic");
  const [addPriority, setAddPriority] = useState("10");
  const [addKey, setAddKey] = useState("");
  const [addTeam, setAddTeam] = useState("");
  const [addChannel, setAddChannel] = useState("");
  const [saving, setSaving] = useState(false);

  const [editId, setEditId] = useState<string | null>(null);
  const [editPriority, setEditPriority] = useState("");
  const [editKey, setEditKey] = useState("");
  const [editTeam, setEditTeam] = useState("");
  const [editChannel, setEditChannel] = useState("");
  const [editSaving, setEditSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`${apiBase}/admin/routing-rules`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 403) { setErr("Admin role required to manage routing rules."); setRules([]); return; }
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      const j = (await r.json()) as { items: Rule[] };
      setRules(j.items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load routing rules.");
    } finally {
      setLoading(false);
    }
  }, [token, apiBase]);

  useEffect(() => { void load(); }, [load]);

  async function addRule(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr(null);
    try {
      const body = addType === "topic"
        ? { rule_type: "topic", priority: Number(addPriority), impact_category: addKey, team_slug: addTeam, channel_slug: addChannel }
        : { rule_type: "severity", priority: Number(addPriority), severity_value: addKey, team_slug: addTeam, channel_slug: addChannel };
      const r = await fetch(`${apiBase}/admin/routing-rules`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      setAddKey(""); setAddTeam(""); setAddChannel(""); setAddPriority("10");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to add rule.");
    } finally {
      setSaving(false);
    }
  }

  function startEdit(rule: Rule) {
    setEditId(rule.id);
    setEditPriority(String(rule.priority));
    setEditKey(rule.impact_category ?? rule.severity_value ?? "");
    setEditTeam(rule.team_slug);
    setEditChannel(rule.channel_slug);
  }

  async function saveEdit(ruleId: string, kind: "topic" | "severity") {
    setEditSaving(true);
    setErr(null);
    try {
      const patch: Record<string, unknown> = {
        priority: Number(editPriority),
        team_slug: editTeam,
        channel_slug: editChannel,
      };
      if (kind === "topic") patch.impact_category = editKey;
      else patch.severity_value = editKey;
      const r = await fetch(`${apiBase}/admin/routing-rules/${ruleId}`, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      setEditId(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to save rule.");
    } finally {
      setEditSaving(false);
    }
  }

  async function deleteRule(id: string) {
    if (!window.confirm("Delete this routing rule?")) return;
    try {
      const r = await fetch(`${apiBase}/admin/routing-rules/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to delete rule.");
    }
  }

  const topicRules = rules.filter((r) => r.rule_type === "topic");
  const severityRules = rules.filter((r) => r.rule_type === "severity");

  return (
    <div style={{ maxWidth: 920 }}>
      {err && <div className="error-banner" role="alert">{err}</div>}

      {isAdmin && (
        <div className="rule-form">
          <div className="rule-form-title">Add New Rule</div>
          <form onSubmit={addRule}>
            <div className="rule-form-grid">
              <div className="form-group">
                <label className="form-label">Rule type</label>
                <select
                  className="form-select"
                  value={addType}
                  onChange={(e) => setAddType(e.target.value as "topic" | "severity")}
                >
                  <option value="topic">Topic (impact category)</option>
                  <option value="severity">Severity (escalation)</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Priority</label>
                <input
                  className="form-input"
                  type="number"
                  value={addPriority}
                  onChange={(e) => setAddPriority(e.target.value)}
                  required
                  min="1"
                />
              </div>
              <div className="form-group">
                <label className="form-label">{addType === "topic" ? "Impact Category" : "Severity"}</label>
                <input
                  className="form-input"
                  placeholder={addType === "topic" ? "e.g. drug_safety" : "e.g. critical"}
                  value={addKey}
                  onChange={(e) => setAddKey(e.target.value)}
                  required
                />
              </div>
              <div className="form-group">
                <label className="form-label">Team Slug</label>
                <input
                  className="form-input"
                  placeholder="regulatory-affairs"
                  value={addTeam}
                  onChange={(e) => setAddTeam(e.target.value)}
                  required
                />
              </div>
              <div className="form-group">
                <label className="form-label">Channel Slug</label>
                <input
                  className="form-input"
                  placeholder="alerts-critical"
                  value={addChannel}
                  onChange={(e) => setAddChannel(e.target.value)}
                  required
                />
              </div>
              <div className="form-group" style={{ justifyContent: "flex-end" }}>
                <label className="form-label" style={{ visibility: "hidden" }}>.</label>
                <button type="submit" className="btn btn-primary" disabled={saving}>
                  {saving ? <><div className="spinner" style={{ width: 14, height: 14 }} />Adding…</> : "Add rule"}
                </button>
              </div>
            </div>
          </form>
        </div>
      )}

      {!isAdmin && (
        <div style={{ padding: "14px 16px", background: "rgba(255,255,255,0.03)", border: "1px solid var(--card-border)", borderRadius: "var(--radius-sm)", marginBottom: 20, fontSize: 13, color: "var(--text-secondary)" }}>
          Admin role required to manage routing rules. You can view them below.
        </div>
      )}

      {loading ? (
        <div className="loading-center"><div className="spinner" /></div>
      ) : (
        <>
          <RuleTable title="Topic Rules" desc="Map impact categories to team channels." rules={topicRules} kind="topic"
            editId={editId} editPriority={editPriority} editKey={editKey} editTeam={editTeam} editChannel={editChannel}
            editSaving={editSaving} isAdmin={isAdmin}
            onEdit={startEdit} onSave={saveEdit} onCancelEdit={() => setEditId(null)}
            onDelete={deleteRule}
            setEditPriority={setEditPriority} setEditKey={setEditKey} setEditTeam={setEditTeam} setEditChannel={setEditChannel}
          />
          <RuleTable title="Severity Rules" desc="Escalate alerts by severity level." rules={severityRules} kind="severity"
            editId={editId} editPriority={editPriority} editKey={editKey} editTeam={editTeam} editChannel={editChannel}
            editSaving={editSaving} isAdmin={isAdmin}
            onEdit={startEdit} onSave={saveEdit} onCancelEdit={() => setEditId(null)}
            onDelete={deleteRule}
            setEditPriority={setEditPriority} setEditKey={setEditKey} setEditTeam={setEditTeam} setEditChannel={setEditChannel}
          />
        </>
      )}
    </div>
  );
}

function RuleTable({
  title, desc, rules, kind, editId, editPriority, editKey, editTeam, editChannel, editSaving, isAdmin,
  onEdit, onSave, onCancelEdit, onDelete, setEditPriority, setEditKey, setEditTeam, setEditChannel,
}: {
  title: string; desc: string; rules: Rule[]; kind: "topic" | "severity";
  editId: string | null; editPriority: string; editKey: string; editTeam: string; editChannel: string;
  editSaving: boolean; isAdmin: boolean;
  onEdit: (r: Rule) => void; onSave: (id: string, kind: "topic" | "severity") => Promise<void>;
  onCancelEdit: () => void; onDelete: (id: string) => Promise<void>;
  setEditPriority: (v: string) => void; setEditKey: (v: string) => void;
  setEditTeam: (v: string) => void; setEditChannel: (v: string) => void;
}) {
  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text)" }}>{title}</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 3 }}>{desc}</div>
      </div>
      <div style={{ overflowX: "auto", borderRadius: "var(--radius-sm)", border: "1px solid var(--card-border)", background: "var(--card-bg)" }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Priority</th>
              <th>{kind === "topic" ? "Impact Category" : "Severity"}</th>
              <th>Team</th>
              <th>Channel</th>
              {isAdmin && <th></th>}
            </tr>
          </thead>
          <tbody>
            {rules.length === 0 ? (
              <tr>
                <td colSpan={isAdmin ? 5 : 4} style={{ color: "var(--text-dim)", padding: "20px 16px", textAlign: "center" }}>
                  No {title.toLowerCase()} configured yet.
                </td>
              </tr>
            ) : rules.map((rule) => (
              editId === rule.id ? (
                <tr key={rule.id}>
                  <td colSpan={isAdmin ? 5 : 4} style={{ padding: "10px 16px" }}>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
                      <div className="form-group" style={{ width: 80 }}>
                        <label className="form-label">Priority</label>
                        <input className="form-input" type="number" value={editPriority} onChange={(e) => setEditPriority(e.target.value)} />
                      </div>
                      <div className="form-group" style={{ flex: 1, minWidth: 120 }}>
                        <label className="form-label">{kind === "topic" ? "Impact Category" : "Severity"}</label>
                        <input className="form-input" value={editKey} onChange={(e) => setEditKey(e.target.value)} />
                      </div>
                      <div className="form-group" style={{ flex: 1, minWidth: 120 }}>
                        <label className="form-label">Team</label>
                        <input className="form-input" value={editTeam} onChange={(e) => setEditTeam(e.target.value)} />
                      </div>
                      <div className="form-group" style={{ flex: 1, minWidth: 120 }}>
                        <label className="form-label">Channel</label>
                        <input className="form-input" value={editChannel} onChange={(e) => setEditChannel(e.target.value)} />
                      </div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button className="btn btn-primary btn-sm" type="button" disabled={editSaving} onClick={() => void onSave(rule.id, kind)}>
                          {editSaving ? "Saving…" : "Save"}
                        </button>
                        <button className="btn btn-secondary btn-sm" type="button" onClick={onCancelEdit}>Cancel</button>
                      </div>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={rule.id}>
                  <td style={{ fontWeight: 600 }}>{rule.priority}</td>
                  <td>{kind === "topic" ? rule.impact_category : rule.severity_value}</td>
                  <td>{rule.team_slug}</td>
                  <td>{rule.channel_slug}</td>
                  {isAdmin && (
                    <td>
                      <div className="flex gap-6">
                        <button className="btn btn-ghost btn-xs" type="button" onClick={() => onEdit(rule)}>Edit</button>
                        <button className="btn btn-danger btn-xs" type="button" onClick={() => void onDelete(rule.id)}>Delete</button>
                      </div>
                    </td>
                  )}
                </tr>
              )
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ============================================================
   CLASSIFICATION POLICY TAB
   ============================================================ */

function PolicyTab() {
  const { token, apiBase, me } = useAuth();
  const isAdmin = me?.role === "admin";
  const [state, setState] = useState<PolicyState | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const [draftThreshold, setDraftThreshold] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftReason, setDraftReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`${apiBase}/admin/classification-policy`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 403) { setErr("Admin role required."); return; }
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      const j = (await r.json()) as PolicyState;
      setState(j);
      if (j.draft) {
        setDraftThreshold(String(j.draft.low_confidence_threshold));
        setDraftPrompt(j.draft.system_prompt);
      } else {
        setDraftThreshold(String(j.active.low_confidence_threshold));
        setDraftPrompt(j.active.system_prompt);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load policy.");
    } finally {
      setLoading(false);
    }
  }, [token, apiBase]);

  useEffect(() => { void load(); }, [load]);

  async function saveDraft(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr(null);
    setOk(null);
    try {
      const r = await fetch(`${apiBase}/admin/classification-policy/draft`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          low_confidence_threshold: Number(draftThreshold),
          system_prompt: draftPrompt,
          reason: draftReason || null,
        }),
      });
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      setOk("Draft saved.");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to save draft.");
    } finally {
      setSaving(false);
    }
  }

  async function applyDraft() {
    if (!window.confirm("Apply the draft policy to production? This will affect all future classifications.")) return;
    setApplying(true);
    setErr(null);
    setOk(null);
    try {
      const r = await fetch(`${apiBase}/admin/classification-policy/apply`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Applied via Settings UI" }),
      });
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      setOk("Policy applied to production.");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to apply policy.");
    } finally {
      setApplying(false);
    }
  }

  if (loading) return <div className="loading-center"><div className="spinner" /></div>;

  return (
    <div style={{ maxWidth: 820 }}>
      {err && <div className="error-banner" role="alert">{err}</div>}
      {ok && <div className="success-banner" role="status">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <circle cx="7" cy="7" r="6" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4.5 7l2 2 3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {ok}
      </div>}

      {state && (
        <>
          <div className="policy-block">
            <div className="policy-block-label">Active Policy (v{state.active.version})</div>
            <div className="two-col">
              <div className="form-group">
                <label className="form-label">Confidence Threshold</label>
                <div style={{ fontSize: 22, fontWeight: 700, color: "var(--text)" }}>
                  {state.active.low_confidence_threshold}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  Updates below this score are sent for human review.
                </div>
              </div>
            </div>
          </div>

          {isAdmin && (
            <form onSubmit={saveDraft}>
              <div className="policy-block">
                <div className="policy-block-label">Edit Draft Policy</div>
                <div className="form-group" style={{ marginBottom: 14 }}>
                  <label className="form-label">Low Confidence Threshold (0–1)</label>
                  <input
                    className="form-input"
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                    value={draftThreshold}
                    onChange={(e) => setDraftThreshold(e.target.value)}
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 14 }}>
                  <label className="form-label">System Prompt</label>
                  <textarea
                    className="form-textarea"
                    rows={8}
                    value={draftPrompt}
                    onChange={(e) => setDraftPrompt(e.target.value)}
                    placeholder="Enter classification system prompt…"
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 16 }}>
                  <label className="form-label">Change Reason</label>
                  <input
                    className="form-input"
                    placeholder="Optional: describe why this change is needed"
                    value={draftReason}
                    onChange={(e) => setDraftReason(e.target.value)}
                  />
                </div>
                <div className="flex gap-10">
                  <button type="submit" className="btn btn-secondary" disabled={saving}>
                    {saving ? <><div className="spinner" style={{ width: 14, height: 14 }} />Saving…</> : "Save draft"}
                  </button>
                  {state.draft && (
                    <button type="button" className="btn btn-primary" disabled={applying} onClick={() => void applyDraft()}>
                      {applying ? "Applying…" : "Apply to production"}
                    </button>
                  )}
                </div>
              </div>
            </form>
          )}
        </>
      )}
    </div>
  );
}

/* ============================================================
   AUDIT LOG TAB
   ============================================================ */

function AuditTab() {
  const { token, apiBase } = useAuth();
  const [items, setItems] = useState<AuditItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  const [filterAction, setFilterAction] = useState("");
  const [filterRunId, setFilterRunId] = useState("");
  const [refresh, setRefresh] = useState(0);

  const PAGE_SIZE = 50;

  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      setLoading(true);
      setErr(null);
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(offset),
      });
      if (filterAction) params.set("action", filterAction);
      if (filterRunId.trim()) params.set("run_id", filterRunId.trim());
      try {
        const r = await fetch(`${apiBase}/audit-events?${params}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (!r.ok) { setErr(await readErrorMessage(r)); return; }
        const j = (await r.json()) as { items: AuditItem[]; total: number };
        setItems(j.items);
        setTotal(j.total);
      } catch { /* ignore */ }
      finally { if (!ctrl.signal.aborted) setLoading(false); }
    })();
    return () => ctrl.abort();
  }, [token, apiBase, offset, refresh]);

  function applyFilters(e: FormEvent) {
    e.preventDefault();
    setOffset(0);
    setRefresh((n) => n + 1);
  }

  return (
    <div style={{ maxWidth: 1000 }}>
      {err && <div className="error-banner" role="alert">{err}</div>}

      <form className="audit-filter-bar" onSubmit={applyFilters}>
        <div className="form-group" style={{ minWidth: 200 }}>
          <label className="form-label">Event type</label>
          <select className="form-select" value={filterAction} onChange={(e) => setFilterAction(e.target.value)}>
            <option value="">All events</option>
            {Object.entries(ACTION_LABELS).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </div>
        <div className="form-group" style={{ minWidth: 220 }}>
          <label className="form-label">Run ID</label>
          <input
            className="form-input"
            placeholder="Filter by run ID…"
            value={filterRunId}
            onChange={(e) => setFilterRunId(e.target.value)}
          />
        </div>
        <div className="form-group" style={{ justifyContent: "flex-end" }}>
          <label className="form-label" style={{ visibility: "hidden" }}>.</label>
          <button type="submit" className="btn btn-secondary btn-sm">Apply</button>
        </div>
      </form>

      {loading ? (
        <div className="loading-center"><div className="spinner" /></div>
      ) : (
        <div style={{ overflowX: "auto", borderRadius: "var(--radius-sm)", border: "1px solid var(--card-border)", background: "var(--card-bg)" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Event</th>
                <th>When</th>
                <th>Run ID</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={4} style={{ textAlign: "center", color: "var(--text-dim)", padding: "32px 16px" }}>
                    No audit events found.
                  </td>
                </tr>
              ) : items.map((ev) => (
                <tr key={ev.id}>
                  <td>
                    <span style={{ fontWeight: 500 }}>
                      {ACTION_LABELS[ev.action] ?? ev.action.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                    {formatDate(ev.created_at)}
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-dim)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {ev.run_id ? ev.run_id.slice(0, 16) + "…" : "—"}
                  </td>
                  <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {ev.source_id ? ev.source_id.slice(0, 12) + "…" : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && total > 0 && (
        <div className="flex items-center gap-10" style={{ marginTop: 12 }}>
          <button className="btn btn-secondary btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} type="button">
            ← Prev
          </button>
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button className="btn btn-secondary btn-sm" disabled={offset + items.length >= total} onClick={() => setOffset(offset + PAGE_SIZE)} type="button">
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
