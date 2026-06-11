import { useEffect, useState } from 'react';
import { Target, Rocket, Trash2, Crosshair, Shield, Radio, Plus, X } from 'lucide-react';
import './MissionDeck.css';

interface MissionDeckProps {
  token: string;
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
  launched_at: string | null;
  created_at: string;
  squad: SquadMember[];
  progress: Progress;
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

const MissionDeck: React.FC<MissionDeckProps> = ({ token }) => {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [toast, setToast] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  // New-mission form state
  const [title, setTitle] = useState('');
  const [targetUrl, setTargetUrl] = useState('');
  const [narrativeGoal, setNarrativeGoal] = useState('');
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
    setTactic('soft_support');
    setPendingSquad([]);
    setRoleSelect({ alpha: '', beta: '', gamma: '' });
  };

  const handleCreate = async () => {
    if (!title.trim() || !targetUrl.trim()) {
      showToast('Title and Target URL are required.', 'error');
      return;
    }
    if (!pendingSquad.some(m => m.assigned_role === 'alpha')) {
      showToast('Assign at least one Alpha agent to seed the narrative.', 'error');
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
          tactic,
          squad: pendingSquad,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`Mission "${data.title}" created.`, 'success');
        resetForm();
        fetchMissions();
      } else {
        showToast(`Failed: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleLaunch = async (id: number) => {
    try {
      const res = await fetch(`/api/v1/missions/${id}/launch`, { method: 'POST', headers });
      const data = await res.json();
      if (res.ok) {
        showToast('DAG launched — Alpha wave dispatched.', 'success');
        fetchMissions();
      } else {
        showToast(`Launch failed: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this mission?')) return;
    try {
      const res = await fetch(`/api/v1/missions/${id}`, { method: 'DELETE', headers });
      if (res.ok) {
        showToast('Mission deleted.', 'success');
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
            <input value={targetUrl} onChange={e => setTargetUrl(e.target.value)} placeholder="https://instagram.com/p/..." />
          </div>
          <div className="md-field">
            <label>Narrative Goal</label>
            <textarea rows={4} value={narrativeGoal} onChange={e => setNarrativeGoal(e.target.value)}
              placeholder="The core message the Alpha will plant and the squad will reinforce." />
          </div>
          <div className="md-field">
            <label>Tactic</label>
            <select value={tactic} onChange={e => setTactic(e.target.value)}>
              {TACTICS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
        </div>

        {/* ── Squad Assembly ── */}
        <div className="md-squad-assembly">
          <h3>Squad Assembly</h3>
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
            <div key={m.id} className="md-mission-card">
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
                    {s.agent_id}:{s.assigned_role}
                  </span>
                ))}
              </div>

              <div className="md-mission-actions">
                {(m.status === 'pending' || m.status === 'failed') && (
                  <button className="btn-primary" onClick={() => handleLaunch(m.id)}>
                    <Rocket size={14} /> {m.status === 'failed' ? 'Relaunch' : 'Launch DAG'}
                  </button>
                )}
                <button className="btn-icon text-danger" onClick={() => handleDelete(m.id)} title="Delete">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default MissionDeck;
