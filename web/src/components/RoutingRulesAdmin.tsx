import { type FormEvent, useCallback, useEffect, useId, useState } from "react";

import { readErrorMessage } from "../httpErrors";

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

type ListResponse = { items: Rule[] };

type Props = {
  apiBase: string;
  token: string;
  onUnauthorized: () => void;
};

export function RoutingRulesAdmin({ apiBase, token, onUnauthorized }: Props) {
  const sectionId = useId();
  const [items, setItems] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [canManage, setCanManage] = useState(true);

  const [addType, setAddType] = useState<"topic" | "severity">("topic");
  const [addPriority, setAddPriority] = useState("10");
  const [addImpact, setAddImpact] = useState("");
  const [addSeverity, setAddSeverity] = useState("");
  const [addTeam, setAddTeam] = useState("");
  const [addChannel, setAddChannel] = useState("");
  const [savingAdd, setSavingAdd] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      const r = await fetch(`${apiBase}/admin/routing-rules`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 401) {
        setItems([]);
        setCanManage(false);
        setErr("Session expired. Log in again.");
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        setItems([]);
        setCanManage(false);
        setErr("Routing rule administration is available to admin accounts only.");
        return;
      }
      if (!r.ok) {
        setItems([]);
        setErr(await readErrorMessage(r));
        return;
      }
      const j = (await r.json()) as ListResponse;
      setCanManage(true);
      setItems(j.items);
    } catch (e) {
      setItems([]);
      setErr(e instanceof Error ? e.message : "Failed to load routing rules.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, onUnauthorized, token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setSavingAdd(true);
    try {
      const priority = Number(addPriority);
      if (!Number.isFinite(priority)) {
        setErr("Priority must be a number.");
        return;
      }
      const body =
        addType === "topic"
          ? {
              rule_type: "topic",
              priority,
              impact_category: addImpact,
              team_slug: addTeam,
              channel_slug: addChannel,
            }
          : {
              rule_type: "severity",
              priority,
              severity_value: addSeverity,
              team_slug: addTeam,
              channel_slug: addChannel,
            };
      const r = await fetch(`${apiBase}/admin/routing-rules`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      if (r.status === 401) {
        setItems([]);
        setCanManage(false);
        setErr("Session expired. Log in again.");
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        setItems([]);
        setCanManage(false);
        setErr("Routing rule administration is available to admin accounts only.");
        return;
      }
      if (!r.ok) {
        setErr(await readErrorMessage(r));
        return;
      }
      setAddImpact("");
      setAddSeverity("");
      setAddTeam("");
      setAddChannel("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Network error saving routing rule.");
    } finally {
      setSavingAdd(false);
    }
  }

  const topicRows = items.filter((x) => x.rule_type === "topic");
  const severityRows = items.filter((x) => x.rule_type === "severity");

  return (
    <section
      aria-labelledby={sectionId}
      style={{ marginTop: "2rem", borderTop: "1px solid #ccc", paddingTop: "1rem" }}
    >
      <h2 id={sectionId}>Routing &amp; escalation rules</h2>
      <p style={{ fontSize: "0.9rem", color: "#444", maxWidth: "52rem" }}>
        <strong>Topic</strong> rules map an impact category to a team and channel.{" "}
        <strong>Severity</strong> rules (escalation) map a severity label to routing targets;
        see resolver docs for precedence.
      </p>
      {loading && <p aria-live="polite">Loading rules…</p>}
      {err && (
        <p role="alert" style={{ color: "crimson", whiteSpace: "pre-wrap" }}>
          {err}
        </p>
      )}

      {canManage ? (
        <form
          onSubmit={onAdd}
          style={{
            marginBottom: "1.5rem",
            padding: "1rem",
            background: "#f7f7f7",
            borderRadius: 6,
          }}
        >
          <h3 style={{ marginTop: 0 }}>Add rule</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            <label>
              Type{" "}
              <select
                value={addType}
                onChange={(e) => setAddType(e.target.value as "topic" | "severity")}
              >
                <option value="topic">Topic (impact category)</option>
                <option value="severity">Severity (escalation)</option>
              </select>
            </label>
            <label>
              Priority{" "}
              <input
                value={addPriority}
                onChange={(e) => setAddPriority(e.target.value)}
                type="number"
                required
                style={{ width: "6rem" }}
              />
            </label>
            {addType === "topic" ? (
              <label>
                Impact category{" "}
                <input
                  value={addImpact}
                  onChange={(e) => setAddImpact(e.target.value)}
                  required
                  style={{ minWidth: "12rem" }}
                />
              </label>
            ) : (
              <label>
                Severity value{" "}
                <input
                  value={addSeverity}
                  onChange={(e) => setAddSeverity(e.target.value)}
                  required
                  style={{ minWidth: "12rem" }}
                />
              </label>
            )}
            <label>
              Team slug{" "}
              <input
                value={addTeam}
                onChange={(e) => setAddTeam(e.target.value)}
                required
                style={{ minWidth: "10rem" }}
              />
            </label>
            <label>
              Channel slug{" "}
              <input
                value={addChannel}
                onChange={(e) => setAddChannel(e.target.value)}
                required
                style={{ minWidth: "10rem" }}
              />
            </label>
            <button type="submit" disabled={savingAdd}>
              {savingAdd ? "Saving…" : "Add rule"}
            </button>
          </div>
        </form>
      ) : (
        <p style={{ color: "#555" }}>
          Routing rule controls are hidden until an admin session is confirmed.
        </p>
      )}

      <h3>Topic rules</h3>
      <RuleTable
        rows={topicRows}
        kind="topic"
        apiBase={apiBase}
        token={token}
        canManage={canManage}
        onAuthFailure={(message) => {
          setItems([]);
          setCanManage(false);
          setErr(message);
        }}
        onUnauthorized={onUnauthorized}
        onMutate={load}
      />

      <h3>Severity (escalation) rules</h3>
      <RuleTable
        rows={severityRows}
        kind="severity"
        apiBase={apiBase}
        token={token}
        canManage={canManage}
        onAuthFailure={(message) => {
          setItems([]);
          setCanManage(false);
          setErr(message);
        }}
        onUnauthorized={onUnauthorized}
        onMutate={load}
      />
    </section>
  );
}

function RuleTable({
  rows,
  kind,
  apiBase,
  token,
  canManage,
  onAuthFailure,
  onUnauthorized,
  onMutate,
}: {
  rows: Rule[];
  kind: "topic" | "severity";
  apiBase: string;
  token: string;
  canManage: boolean;
  onAuthFailure: (message: string) => void;
  onUnauthorized: () => void;
  onMutate: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [patchErr, setPatchErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function onDelete(id: string) {
    if (!window.confirm("Delete this routing rule?")) return;
    try {
      const r = await fetch(`${apiBase}/admin/routing-rules/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 401) {
        onAuthFailure("Session expired. Log in again.");
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        onAuthFailure("Routing rule administration is available to admin accounts only.");
        return;
      }
      if (!r.ok) {
        setPatchErr(await readErrorMessage(r));
        return;
      }
      await onMutate();
    } catch (e) {
      setPatchErr(e instanceof Error ? e.message : "Network error deleting routing rule.");
    }
  }

  return (
    <div style={{ overflowX: "auto" }}>
      {patchErr && (
        <p role="alert" style={{ color: "crimson" }}>
          {patchErr}
        </p>
      )}
      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: "1.5rem" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
            <th scope="col">Priority</th>
            <th scope="col">{kind === "topic" ? "Impact category" : "Severity"}</th>
            <th scope="col">Team</th>
            <th scope="col">Channel</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={5} style={{ padding: "0.5rem 0", color: "#666" }}>
                No rules yet.
              </td>
            </tr>
          ) : (
            rows.map((r) => (
              <tr key={r.id} style={{ borderBottom: "1px solid #eee" }}>
                {editing === r.id ? (
                  <EditRow
                    rule={r}
                    kind={kind}
                    apiBase={apiBase}
                    token={token}
                    saving={saving}
                    setSaving={setSaving}
                    setPatchErr={setPatchErr}
                    onAuthFailure={onAuthFailure}
                    onUnauthorized={onUnauthorized}
                    onDone={async () => {
                      setEditing(null);
                      await onMutate();
                    }}
                    onCancel={() => setEditing(null)}
                  />
                ) : (
                  <>
                    <td style={{ padding: "6px 8px 6px 0" }}>{r.priority}</td>
                    <td style={{ padding: "6px 8px" }}>
                      {kind === "topic" ? r.impact_category : r.severity_value}
                    </td>
                    <td style={{ padding: "6px 8px" }}>{r.team_slug}</td>
                    <td style={{ padding: "6px 8px" }}>{r.channel_slug}</td>
                    <td style={{ padding: "6px 8px" }}>
                      {canManage ? (
                        <>
                          <button type="button" onClick={() => setEditing(r.id)}>
                            Edit
                          </button>{" "}
                          <button type="button" onClick={() => void onDelete(r.id)}>
                            Delete
                          </button>
                        </>
                      ) : (
                        <span style={{ color: "#666" }}>Read-only</span>
                      )}
                    </td>
                  </>
                )}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function EditRow({
  rule,
  kind,
  apiBase,
  token,
  saving,
  setSaving,
  setPatchErr,
  onAuthFailure,
  onUnauthorized,
  onDone,
  onCancel,
}: {
  rule: Rule;
  kind: "topic" | "severity";
  apiBase: string;
  token: string;
  saving: boolean;
  setSaving: (v: boolean) => void;
  setPatchErr: (v: string | null) => void;
  onAuthFailure: (message: string) => void;
  onUnauthorized: () => void;
  onDone: () => Promise<void>;
  onCancel: () => void;
}) {
  const [priority, setPriority] = useState(String(rule.priority));
  const [keyField, setKeyField] = useState(
    kind === "topic" ? (rule.impact_category ?? "") : (rule.severity_value ?? "")
  );
  const [team, setTeam] = useState(rule.team_slug);
  const [channel, setChannel] = useState(rule.channel_slug);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    setPatchErr(null);
    const p = Number(priority);
    if (!Number.isFinite(p)) {
      setPatchErr("Priority must be a number.");
      return;
    }
    const body: Record<string, unknown> = {
      priority: p,
      team_slug: team,
      channel_slug: channel,
    };
    if (kind === "topic") body.impact_category = keyField;
    else body.severity_value = keyField;
    setSaving(true);
    try {
      const r = await fetch(`${apiBase}/admin/routing-rules/${rule.id}`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      if (r.status === 401) {
        onAuthFailure("Session expired. Log in again.");
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        onAuthFailure("Routing rule administration is available to admin accounts only.");
        return;
      }
      if (!r.ok) {
        setPatchErr(await readErrorMessage(r));
        return;
      }
      await onDone();
    } catch (e) {
      setPatchErr(e instanceof Error ? e.message : "Network error updating routing rule.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <td colSpan={5} style={{ padding: "8px 0" }}>
        <form
          onSubmit={(e) => void onSave(e)}
          style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}
        >
          <label>
            Priority{" "}
            <input
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              type="number"
              required
              style={{ width: "5rem" }}
            />
          </label>
          <label>
            {kind === "topic" ? "Impact" : "Severity"}{" "}
            <input
              value={keyField}
              onChange={(e) => setKeyField(e.target.value)}
              required
              style={{ minWidth: "10rem" }}
            />
          </label>
          <label>
            Team{" "}
            <input value={team} onChange={(e) => setTeam(e.target.value)} required />
          </label>
          <label>
            Channel{" "}
            <input value={channel} onChange={(e) => setChannel(e.target.value)} required />
          </label>
          <button type="submit" disabled={saving}>
            Save
          </button>
          <button type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        </form>
      </td>
    </>
  );
}
