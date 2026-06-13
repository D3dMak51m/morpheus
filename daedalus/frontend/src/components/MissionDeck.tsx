import { useEffect, useState } from 'react';
import {
  Target, Rocket, Trash2, Crosshair, Shield, Radio, Plus, X,
  Sparkles, Save, Users, Gauge, Clock, Pencil,
} from 'lucide-react';
import './MissionDeck.css';

interface MissionDeckProps {
  token: string;
  prefill?: { target_url: string; title: string; narrative_goal: string } | null;
  onPrefillConsumed?: () => void;
}

interface AgentProfile {
  agent_id: string;
  full_name: string;
  codename: string;
  caste: string;
}

interface SquadMember {
  id: number;
  agent_id: string;
  assigned_role: 'alpha' | 'beta' | 'gamma';
  status: string;
  codename?: string | null;
}

interface Progress {
  percent: number;
  stage: string;
  done: number;
  total: number;
  by_role: Record<string, { total: number; success: number; failed: number; active: number }>;
}

interface Mission {
  id: number;
  title: string;
  target_url: string;
  platform: string;
  narrative_goal: string | null;
  tactic: string;
  status: string;
  alpha_context: string | null;
  forced_context: string | null;
  launched_at: string | null;
  created_at: string;
  squad: SquadMember[];
  progress: Progress;
}

interface EligibleAgent {
  agent_id: string;
  codename: string | null;
  caste: string;
  status: string;
  platform: string;
  active_mission_load: number;
  at_capacity: boolean;
  already_enlisted: boolean;
  match_score: number;
  match_reasons: string[];
}

interface PendingMember {
  agent_id: string;
  assigned_role: 'alpha' | 'beta' | 'gamma';
}

const TACTICS = [
  { value: 'soft_support', label: 'Soft Support' },
  { value: 'aggressive_displacement', label: 'Aggressive Displacement' },
];

const ROLES: { key: 'alpha' | 'beta' | 'gamma'; label: string; icon: any; hint: string }[] = [
  { key: 'alpha', label: 'Alpha', icon: Crosshair, hint: 'Seeds the narrative first' },
  { key: 'beta', label: 'Beta', icon: Shield, hint: 'Amplifies & defends' },
  { key: 'gamma', label: 'Gamma', icon: Radio, hint: 'Creates supporting noise' },
];

const STATUS_COLORS: Record<string, string> = {
  pending: '#64748b',
  running: '#3b82f6',
  amplifying: '#a855f7',
  completed: '#22c55e',
  failed: '#ef4444',
};

const fmtTime = (iso: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

// ── Mission detail / management modal ───────────────────────────────────────

const MissionDetailModal: React.FC<{
  mission: Mission;
  token: string;
  onClose: () => void;
  onChanged: () => void;
  showToast: (text: string, type: 'success' | 'error') => void;
}> = ({ mission, token, onClose, onChanged, showToast }) => {
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  const editable = mission.status === 'pending' || mission.status === 'failed';

  const [form, setForm] = useState({
    title: mission.title,
    target_url: mission.target_url,
    narrative_goal: mission.narrative_goal || '',
    tactic: mission.tactic,
    forced_context: mission.forced_context || '',
  });
  const [eligible, setEligible] = useState<EligibleAgent[]>([]);
  const [counts, setCounts] = useState({ alpha: 1, beta: 2, gamma: 2 });
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setForm({
      title: mission.title,
      target_url: mission.target_url,
      narrative_goal: mission.narrative_goal || '',
      tactic: mission.tactic,
      forced_context: mission.forced_context || '',
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mission.id]);

  const fetchEligible = async () => {
    try {
      const res = await fetch(`/api/v1/missions/${mission.id}/eligible-agents`, { headers });
      if (res.ok) setEligible(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchEligible();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mission.id, mission.squad.length]);

  const saveParams = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/v1/missions/${mission.id}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({
          title: form.title,
          target_url: form.target_url,
          narrative_goal: form.narrative_goal,
          tactic: form.tactic,
          forced_context: form.forced_context.trim() || null,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast('Mission parameters saved.', 'success');
        onChanged();
      } else {
        showToast(`Save failed: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const addMember = async (agentId: string, role: 'alpha' | 'beta' | 'gamma') => {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/missions/${mission.id}/squad`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ agent_id: agentId, assigned_role: role }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`${agentId} enlisted as ${role}.`, 'success');
        onChanged();
        fetchEligible();
      } else {
        showToast(`${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const removeMember = async (squadId: number) => {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/missions/${mission.id}/squad/${squadId}`, { method: 'DELETE', headers });
      if (res.ok) {
        onChanged();
        fetchEligible();
      } else {
        const data = await res.json();
        showToast(`${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const autoAssign = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/missions/${mission.id}/auto-assign`, {
        method: 'POST',
        headers,
        body: JSON.stringify(counts),
      });
      const data = await res.json();
      if (res.ok) {
        const added = (data.squad?.length ?? 0);
        showToast(`Auto-assign complete — squad now ${added} bot(s).`, 'success');
        onChanged();
        fetchEligible();
      } else {
        showToast(`Auto-assign failed: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const launch = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/missions/${mission.id}/launch`, { method: 'POST', headers });
      const data = await res.json();
      if (res.ok) {
        showToast('DAG launched — Alpha wave dispatched.', 'success');
        onChanged();
      } else {
        showToast(`Launch failed: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const p = mission.progress;

  return (
    <div className="md-modal-overlay" onClick={onClose}>
      <div className="md-modal" onClick={e => e.stopPropagation()}>
        <div className="md-modal-head">
          <div>
            <h2>{mission.title}</h2>
            <p className="text-muted md-mission-url">{mission.platform} · {mission.target_url}</p>
          </div>
          <div className="md-modal-head-right">
            <span className="md-status-badge" style={{ background: STATUS_COLORS[mission.status] || '#475569' }}>
              {mission.status}
            </span>
            <button className="btn-icon" onClick={onClose} title="Close"><X size={18} /></button>
          </div>
        </div>

        <div className="md-modal-body">
          {/* ── Live metrics ── */}
          <section className="md-section">
            <h4><Gauge size={15} /> Metrics</h4>
            <div className="md-progress-wrap">
              <div className="md-progress-bar">
                <div className="md-progress-fill"
                  style={{ width: `${p.percent}%`, background: STATUS_COLORS[mission.status] || '#3b82f6' }} />
              </div>
              <span className="md-progress-stage">{p.stage} · {p.done}/{p.total} ({p.percent}%)</span>
            </div>
            <div className="md-metrics-grid">
              {ROLES.map(role => {
                const s = p.by_role[role.key] || { total: 0, success: 0, failed: 0, active: 0 };
                return (
                  <div key={role.key} className={`md-metric role-${role.key}`}>
                    <div className="md-metric-title"><role.icon size={13} /> {role.label}</div>
                    <div className="md-metric-nums">
                      <span title="success">{s.success}✓</span>
                      <span title="failed">{s.failed}✗</span>
                      <span title="in-flight">{s.active}⧖</span>
                      <span className="md-metric-total">/ {s.total}</span>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="md-meta-row">
              <span><Clock size={12} /> Created {fmtTime(mission.created_at)}</span>
              <span><Rocket size={12} /> Launched {fmtTime(mission.launched_at)}</span>
            </div>
            {mission.alpha_context && (
              <p className="md-alpha-context"><strong>Alpha context:</strong> {mission.alpha_context}</p>
            )}
          </section>

          {/* ── Parameters (editable when pending/failed) ── */}
          <section className="md-section">
            <h4><Pencil size={15} /> Parameters {editable ? '' : '(read-only — mission in flight)'}</h4>
            <div className="md-edit-grid">
              <div className="md-field">
                <label>Title</label>
                <input value={form.title} disabled={!editable}
                  onChange={e => setForm({ ...form, title: e.target.value })} />
              </div>
              <div className="md-field">
                <label>Target URL</label>
                <input value={form.target_url} disabled={!editable}
                  onChange={e => setForm({ ...form, target_url: e.target.value })} />
              </div>
              <div className="md-field">
                <label>Tactic</label>
                <select value={form.tactic} disabled={!editable}
                  onChange={e => setForm({ ...form, tactic: e.target.value })}>
                  {TACTICS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              <div className="md-field md-field-wide">
                <label>Narrative Goal</label>
                <textarea rows={3} value={form.narrative_goal} disabled={!editable}
                  onChange={e => setForm({ ...form, narrative_goal: e.target.value })} />
              </div>
              <div className="md-field md-field-wide">
                <label>Forced Context <span className="md-hint">(RAG bypass)</span></label>
                <textarea rows={2} value={form.forced_context} disabled={!editable}
                  onChange={e => setForm({ ...form, forced_context: e.target.value })} />
              </div>
            </div>
            {editable && (
              <button className="btn-primary" onClick={saveParams} disabled={saving}>
                <Save size={14} /> {saving ? 'Saving…' : 'Save Parameters'}
              </button>
            )}
          </section>

          {/* ── Squad ── */}
          <section className="md-section">
            <h4><Users size={15} /> Squad ({mission.squad.length})</h4>
            <div className="md-squad-list">
              {mission.squad.length === 0 && <p className="text-muted">No bots enlisted yet.</p>}
              {mission.squad.map(s => (
                <div key={s.id} className={`md-squad-row role-${s.assigned_role} status-${s.status}`}>
                  <span className="md-squad-role">{s.assigned_role}</span>
                  <span className="md-squad-name">{s.codename || s.agent_id}</span>
                  <span className="md-squad-status">{s.status}</span>
                  {editable && (
                    <button className="btn-icon text-danger" title="Remove" onClick={() => removeMember(s.id)}>
                      <X size={14} />
                    </button>
                  )}
                </div>
              ))}
            </div>

            {editable && (
              <div className="md-assign-box">
                <div className="md-assign-row">
                  <Sparkles size={14} />
                  <span>Auto-assign matching bots:</span>
                  {ROLES.map(r => (
                    <label key={r.key} className="md-count-input">
                      {r.label[0]}
                      <input type="number" min={0} max={20} value={(counts as any)[r.key]}
                        onChange={e => setCounts({ ...counts, [r.key]: Math.max(0, parseInt(e.target.value) || 0) })} />
                    </label>
                  ))}
                  <button className="btn-primary" onClick={autoAssign} disabled={busy}>
                    <Sparkles size={13} /> Auto-Assign
                  </button>
                </div>

                <div className="md-eligible">
                  <p className="md-eligible-title">Eligible bots (active account on {mission.platform}, &lt; cap):</p>
                  {eligible.length === 0 && (
                    <p className="text-muted">No eligible bots — none have an active {mission.platform} account.</p>
                  )}
                  {eligible.map(a => (
                    <div key={a.agent_id} className={`md-eligible-row ${a.at_capacity ? 'at-cap' : ''}`}>
                      <span className="md-elig-name">{a.codename || a.agent_id}</span>
                      <span className={`md-elig-caste role-${a.caste}`}>{a.caste}</span>
                      <span className="md-elig-score" title={a.match_reasons.join('; ')}>
                        match {Math.round(a.match_score * 100)}%
                      </span>
                      <span className="md-elig-load" title="active missions / cap">
                        {a.active_mission_load}/5
                      </span>
                      {a.already_enlisted ? (
                        <span className="md-elig-tag">enlisted</span>
                      ) : a.at_capacity ? (
                        <span className="md-elig-tag cap">at cap</span>
                      ) : (
                        <div className="md-elig-add">
                          {ROLES.map(r => (
                            <button key={r.key} className={`md-elig-add-btn role-${r.key}`} disabled={busy}
                              title={`Add as ${r.label}`} onClick={() => addMember(a.agent_id, r.key)}>
                              +{r.label[0]}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>

        {/* ── Footer actions ── */}
        <div className="md-modal-foot">
          {editable && (
            <button className="btn-primary" onClick={launch} disabled={busy}>
              <Rocket size={14} /> {mission.status === 'failed' ? 'Relaunch DAG' : 'Launch DAG'}
            </button>
          )}
          <span className="md-foot-spacer" />
          <span className="text-muted md-cap-note">Per-bot cap: 5 active missions</span>
        </div>
      </div>
    </div>
  );
};

// ── Mission Deck ─────────────────────────────────────────────────────────────

const MissionDeck: React.FC<MissionDeckProps> = ({ token, prefill, onPrefillConsumed }) => {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [toast, setToast] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // New-mission form state
  const [title, setTitle] = useState('');
  const [targetUrl, setTargetUrl] = useState('');
  const [narrativeGoal, setNarrativeGoal] = useState('');
  const [forcedContext, setForcedContext] = useState('');
  const [tactic, setTactic] = useState('soft_support');
  const [pendingSquad, setPendingSquad] = useState<PendingMember[]>([]);
  const [roleSelect, setRoleSelect] = useState<Record<string, string>>({ alpha: '', beta: '', gamma: '' });
  const [creating, setCreating] = useState(false);

  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

  const showToast = (text: string, type: 'success' | 'error') => {
    setToast({ text, type });
    setTimeout(() => setToast(null), 4000);
  };

  useEffect(() => {
    fetchAgents();
    fetchMissions();
    const interval = setInterval(fetchMissions, 5000);
    return () => clearInterval(interval);
  }, []);

  // Pre-fill the builder when arriving from a Scouting Radar conversion.
  useEffect(() => {
    if (prefill) {
      setTitle(prefill.title || '');
      setTargetUrl(prefill.target_url || '');
      setNarrativeGoal(prefill.narrative_goal || '');
      fetchMissions();
      onPrefillConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  const fetchAgents = async () => {
    try {
      const res = await fetch('/api/v1/souls/profiles', { headers });
      if (res.ok) setAgents(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchMissions = async () => {
    try {
      const res = await fetch('/api/v1/missions', { headers });
      if (res.ok) setMissions(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const assignToRole = (role: 'alpha' | 'beta' | 'gamma') => {
    const agentId = roleSelect[role];
    if (!agentId) return;
    if (pendingSquad.some(m => m.agent_id === agentId)) {
      showToast(`${agentId} is already assigned in this mission.`, 'error');
      return;
    }
    setPendingSquad([...pendingSquad, { agent_id: agentId, assigned_role: role }]);
    setRoleSelect({ ...roleSelect, [role]: '' });
  };

  const removePending = (agentId: string) => {
    setPendingSquad(pendingSquad.filter(m => m.agent_id !== agentId));
  };

  const resetForm = () => {
    setTitle('');
    setTargetUrl('');
    setNarrativeGoal('');
    setForcedContext('');
    setTactic('soft_support');
    setPendingSquad([]);
    setRoleSelect({ alpha: '', beta: '', gamma: '' });
  };

  const handleCreate = async () => {
    if (!title.trim() || !targetUrl.trim()) {
      showToast('Title and Target URL are required.', 'error');
      return;
    }
    setCreating(true);
    try {
      const res = await fetch('/api/v1/missions', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          title,
          target_url: targetUrl,
          narrative_goal: narrativeGoal,
          forced_context: forcedContext.trim() || null,
          tactic,
          squad: pendingSquad,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`Mission "${data.title}" created. Open it to auto-assign bots & launch.`, 'success');
        resetForm();
        fetchMissions();
        setSelectedId(data.id);
      } else {
        showToast(`Failed: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this mission?')) return;
    try {
      const res = await fetch(`/api/v1/missions/${id}`, { method: 'DELETE', headers });
      if (res.ok) {
        showToast('Mission deleted.', 'success');
        if (selectedId === id) setSelectedId(null);
        fetchMissions();
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    }
  };

  const agentLabel = (agentId: string) => {
    const a = agents.find(x => x.agent_id === agentId);
    return a ? `${a.full_name} (${agentId})` : agentId;
  };

  const squadByRole = (role: string) => pendingSquad.filter(m => m.assigned_role === role);

  const selected = missions.find(m => m.id === selectedId) || null;

  return (
    <div className="mission-deck view-container">
      {toast && (
        <div className={`md-toast ${toast.type}`}>{toast.text}</div>
      )}

      <div className="header-row">
        <div>
          <h1><Target size={22} style={{ verticalAlign: '-4px' }} /> Mission Deck</h1>
          <p className="subtitle">Plan coordinated DAG campaigns: Alpha seeds → Betas amplify → Gammas swarm.</p>
        </div>
      </div>

      {/* ── Mission Builder ── */}
      <div className="md-builder">
        <div className="md-builder-form">
          <h3>New Mission</h3>
          <div className="md-field">
            <label>Title</label>
            <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Operation codename" />
          </div>
          <div className="md-field">
            <label>Target URL</label>
            <input value={targetUrl} onChange={e => setTargetUrl(e.target.value)} placeholder="https://t.me/channel/123" />
          </div>
          <div className="md-field">
            <label>Narrative Goal</label>
            <textarea rows={4} value={narrativeGoal} onChange={e => setNarrativeGoal(e.target.value)}
              placeholder="The core message the Alpha will plant and the squad will reinforce." />
          </div>
          <div className="md-field">
            <label>Forced Context <span style={{ color: '#94a3b8', fontWeight: 400 }}>(optional)</span></label>
            <textarea rows={3} value={forcedContext} onChange={e => setForcedContext(e.target.value)}
              placeholder="Pin an exact fact here. If set, ORPHEUS injects this verbatim and SKIPS the MUNINN vector search (RAG bypass)." />
          </div>
          <div className="md-field">
            <label>Tactic</label>
            <select value={tactic} onChange={e => setTactic(e.target.value)}>
              {TACTICS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
        </div>

        {/* ── Squad Assembly (optional — can also auto-assign after creating) ── */}
        <div className="md-squad-assembly">
          <h3>Squad Assembly <span className="md-hint">(optional — auto-assign available after create)</span></h3>
          <div className="md-role-cols">
            {ROLES.map(role => {
              const Icon = role.icon;
              return (
                <div key={role.key} className={`md-role-col role-${role.key}`}>
                  <div className="md-role-head">
                    <Icon size={16} /> <strong>{role.label}</strong>
                  </div>
                  <p className="md-role-hint">{role.hint}</p>
                  <div className="md-role-assign">
                    <select value={roleSelect[role.key]} onChange={e => setRoleSelect({ ...roleSelect, [role.key]: e.target.value })}>
                      <option value="">Select agent…</option>
                      {agents
                        .filter(a => !pendingSquad.some(m => m.agent_id === a.agent_id))
                        .map(a => <option key={a.agent_id} value={a.agent_id}>{a.full_name} ({a.caste})</option>)}
                    </select>
                    <button className="btn-icon text-success" onClick={() => assignToRole(role.key)}><Plus size={14} /></button>
                  </div>
                  <div className="md-role-members">
                    {squadByRole(role.key).map(m => (
                      <div key={m.agent_id} className="md-member-chip">
                        <span>{agentLabel(m.agent_id)}</span>
                        <button onClick={() => removePending(m.agent_id)}><X size={12} /></button>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
          <button className="btn-primary md-create-btn" onClick={handleCreate} disabled={creating}>
            {creating ? 'Creating…' : 'Create Mission'}
          </button>
        </div>
      </div>

      {/* ── Active Missions Dashboard ── */}
      <div className="md-dashboard">
        <h2>Active Missions</h2>
        {missions.length === 0 && <p className="text-muted">No missions yet. Build one above.</p>}
        <div className="md-mission-list">
          {missions.map(m => (
            <div key={m.id} className="md-mission-card md-mission-card-clickable" onClick={() => setSelectedId(m.id)}>
              <div className="md-mission-head">
                <div>
                  <h3>{m.title}</h3>
                  <p className="text-muted md-mission-url">{m.platform} · {m.target_url}</p>
                </div>
                <span className="md-status-badge" style={{ background: STATUS_COLORS[m.status] || '#475569' }}>
                  {m.status}
                </span>
              </div>

              <div className="md-progress-wrap">
                <div className="md-progress-bar">
                  <div className="md-progress-fill"
                    style={{ width: `${m.progress.percent}%`, background: STATUS_COLORS[m.status] || '#3b82f6' }} />
                </div>
                <span className="md-progress-stage">{m.progress.stage} · {m.progress.done}/{m.progress.total}</span>
              </div>

              <div className="md-wave-row">
                {ROLES.map(role => {
                  const stats = m.progress.by_role[role.key] || { total: 0, success: 0, failed: 0, active: 0 };
                  if (stats.total === 0) return null;
                  return (
                    <div key={role.key} className={`md-wave-pill role-${role.key}`}>
                      <strong>{role.label}</strong>
                      <span>{stats.success}✓ {stats.failed}✗ {stats.active}⧖ / {stats.total}</span>
                    </div>
                  );
                })}
              </div>

              <div className="md-squad-line">
                {m.squad.map(s => (
                  <span key={s.id} className={`md-squad-tag role-${s.assigned_role} status-${s.status}`}>
                    {s.codename || s.agent_id}:{s.assigned_role}
                  </span>
                ))}
              </div>

              <div className="md-mission-actions" onClick={e => e.stopPropagation()}>
                <button className="btn-secondary" onClick={() => setSelectedId(m.id)}>
                  <Pencil size={14} /> Manage
                </button>
                <button className="btn-icon text-danger" onClick={() => handleDelete(m.id)} title="Delete">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {selected && (
        <MissionDetailModal
          mission={selected}
          token={token}
          onClose={() => setSelectedId(null)}
          onChanged={fetchMissions}
          showToast={showToast}
        />
      )}
    </div>
  );
};

export default MissionDeck;
