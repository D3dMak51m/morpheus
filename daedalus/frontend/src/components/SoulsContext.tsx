import { useState, useEffect } from 'react';
import './SoulsContext.css';

// ── Strict client-side persona shapes (mirror Daedalus Pydantic schemas) ──
interface CommunicationStyle {
  tone_level: number;
  vocab_level: number;
  emoji_frequency: number;
  aggression: number;
  quirks: string[];
}

interface BehavioralRules {
  rules: string[];
  min_delay_between_posts_sec: number;
  max_posts_per_hour: number;
}

interface Account {
  id: number;
  agent_id: string | null;
  platform: string;
  username: string;
  status: string;
  device_id: string | null;
}

interface Profile {
  id: number;
  agent_id: string;
  codename: string;
  full_name: string;
  caste: string;
  status: string;
  profession: string | null;
  residence_city: string | null;
  platforms: string[];
  active_hours_start: number;
  active_hours_end: number;
  communication_style: CommunicationStyle | null;
  behavioral_rules: BehavioralRules | Record<string, any> | null;
  core_mission: string | null;
  current_stance_modifiers: Record<string, string> | null;
  context_subscriptions: string[] | null;
}

interface StancePair {
  topic: string;
  stance: string;
}

interface SoulsContextProps {
  token: string;
}

const PLATFORMS = ['telegram', 'instagram', 'youtube', 'threads', 'web'];
const CASTES = ['alpha', 'beta', 'gamma'];
// Stage 21 — cognitive RAG layer subscriptions.
const SUBSCRIPTION_LAYERS = ['global', 'regional', 'state', 'city', 'personal'];

// Normalize whatever the backend returns into the strict visual shapes.
const normalizeComm = (raw: any): CommunicationStyle => ({
  tone_level: Number(raw?.tone_level ?? 5),
  vocab_level: Number(raw?.vocab_level ?? 5),
  emoji_frequency: Number(raw?.emoji_frequency ?? 3),
  aggression: Number(raw?.aggression ?? 3),
  quirks: Array.isArray(raw?.quirks) ? raw.quirks.map(String) : [],
});

const normalizeRules = (raw: any): BehavioralRules => ({
  rules: Array.isArray(raw?.rules) ? raw.rules.map(String) : [],
  min_delay_between_posts_sec: Number(raw?.min_delay_between_posts_sec ?? 45),
  max_posts_per_hour: Number(raw?.max_posts_per_hour ?? 5),
});

const SoulsContext: React.FC<SoulsContextProps> = ({ token }) => {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState('');

  const [activeTab, setActiveTab] = useState<'identity' | 'psychology' | 'mission' | 'binding' | 'history'>('identity');
  const [historyLogs, setHistoryLogs] = useState<any[]>([]);

  // Stage 23 — decoupled account binding.
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [bindAccountId, setBindAccountId] = useState('');

  // Stance modifiers are edited as visual topic/stance pairs, compiled to a dict on save.
  const [stancePairs, setStancePairs] = useState<StancePair[]>([]);

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  useEffect(() => {
    fetchProfiles();
  }, []);

  useEffect(() => {
    if (selectedProfile) {
      const stance = selectedProfile.current_stance_modifiers || {};
      setStancePairs(Object.entries(stance).map(([topic, s]) => ({ topic, stance: String(s) })));
      setSaveError('');
      if (activeTab === 'history') {
        fetchHistory(selectedProfile.agent_id);
      }
      if (activeTab === 'binding') {
        fetchAccounts();
        setBindAccountId('');
      }
    }
  }, [selectedProfile?.agent_id, activeTab]);

  const fetchHistory = async (agentId: string) => {
    try {
      const res = await fetch(`/api/v1/souls/profiles/${agentId}/history`, { headers });
      if (res.ok) setHistoryLogs(await res.json());
    } catch (e) {
      console.error('Failed to fetch history', e);
    }
  };

  const handleRollback = async (historyId: number) => {
    if (!selectedProfile) return;
    if (!confirm('Are you sure you want to rollback this profile?')) return;
    try {
      const res = await fetch(`/api/v1/souls/profiles/${selectedProfile.agent_id}/rollback/${historyId}`, {
        method: 'POST',
        headers,
      });
      if (res.ok) {
        fetchProfiles();
        setSelectedProfile(null);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchProfiles = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/v1/souls/profiles', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProfiles(Array.isArray(data) ? data : []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch profiles');
      setProfiles([]);
    }
    setLoading(false);
  };

  const fetchAccounts = async () => {
    try {
      const res = await fetch('/api/v1/souls/accounts', { headers });
      if (res.ok) setAccounts(await res.json());
    } catch (e) {
      console.error('Failed to fetch accounts', e);
    }
  };

  const handleBindAccount = async (accountId: number, agentId: string) => {
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/bind?agent_id=${encodeURIComponent(agentId)}`, {
        method: 'PUT', headers,
      });
      if (res.ok) { await fetchAccounts(); fetchProfiles(); setBindAccountId(''); }
    } catch (e) {
      console.error(e);
    }
  };

  const handleUnbindAccount = async (accountId: number) => {
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/unbind`, { method: 'PUT', headers });
      if (res.ok) { await fetchAccounts(); fetchProfiles(); }
    } catch (e) {
      console.error(e);
    }
  };

  const openProfile = (p: Profile) => {
    // Hydrate into strict visual shapes so the builder never touches raw JSON.
    setSelectedProfile({
      ...p,
      communication_style: normalizeComm(p.communication_style),
      behavioral_rules: normalizeRules(p.behavioral_rules),
      current_stance_modifiers: p.current_stance_modifiers || {},
      context_subscriptions: Array.isArray(p.context_subscriptions) ? p.context_subscriptions : ['global'],
    });
    setActiveTab('identity');
  };

  const handleSave = async () => {
    if (!selectedProfile) return;
    setSaveError('');

    const comm = normalizeComm(selectedProfile.communication_style);
    const rules = normalizeRules(selectedProfile.behavioral_rules);

    // Compile visual stance pairs back into a clean dict (skip empty topics).
    const stanceDict: Record<string, string> = {};
    for (const pair of stancePairs) {
      const t = pair.topic.trim();
      if (t) stanceDict[t] = pair.stance;
    }

    const payload = {
      codename: selectedProfile.codename,
      full_name: selectedProfile.full_name,
      caste: selectedProfile.caste,
      profession: selectedProfile.profession,
      residence_city: selectedProfile.residence_city,
      platforms: selectedProfile.platforms,
      active_hours_start: selectedProfile.active_hours_start,
      active_hours_end: selectedProfile.active_hours_end,
      communication_style: comm,
      behavioral_rules: rules,
      core_mission: selectedProfile.core_mission,
      current_stance_modifiers: stanceDict,
      context_subscriptions: selectedProfile.context_subscriptions || ['global'],
    };

    try {
      const res = await fetch(`/api/v1/souls/profiles/${selectedProfile.agent_id}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : `Failed to save: ${res.status}`);
      }
      fetchProfiles();
      setSelectedProfile(null);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Failed to save');
    }
  };

  const togglePlatform = (platform: string) => {
    if (!selectedProfile) return;
    const current = new Set(selectedProfile.platforms || []);
    if (current.has(platform)) current.delete(platform);
    else current.add(platform);
    setSelectedProfile({ ...selectedProfile, platforms: Array.from(current) });
  };

  const toggleSubscription = (layer: string) => {
    if (!selectedProfile) return;
    const current = new Set(selectedProfile.context_subscriptions || ['global']);
    if (current.has(layer)) current.delete(layer);
    else current.add(layer);
    setSelectedProfile({ ...selectedProfile, context_subscriptions: Array.from(current) });
  };

  // ── Communication style helpers ──
  const comm = (): CommunicationStyle => normalizeComm(selectedProfile?.communication_style);

  const setComm = (key: keyof CommunicationStyle, val: any) => {
    if (!selectedProfile) return;
    setSelectedProfile({
      ...selectedProfile,
      communication_style: { ...comm(), [key]: val },
    });
  };

  const updateQuirk = (idx: number, val: string) => {
    const next = [...comm().quirks];
    next[idx] = val;
    setComm('quirks', next);
  };
  const addQuirk = () => setComm('quirks', [...comm().quirks, '']);
  const removeQuirk = (idx: number) => setComm('quirks', comm().quirks.filter((_, i) => i !== idx));

  // ── Behavioral rule helpers ──
  const rules = (): BehavioralRules => normalizeRules(selectedProfile?.behavioral_rules);

  const setRules = (key: keyof BehavioralRules, val: any) => {
    if (!selectedProfile) return;
    setSelectedProfile({
      ...selectedProfile,
      behavioral_rules: { ...rules(), [key]: val },
    });
  };

  const updateRule = (idx: number, val: string) => {
    const next = [...rules().rules];
    next[idx] = val;
    setRules('rules', next);
  };
  const addRule = () => setRules('rules', [...rules().rules, '']);
  const removeRule = (idx: number) => setRules('rules', rules().rules.filter((_, i) => i !== idx));

  // ── Stance pair helpers ──
  const updateStance = (idx: number, field: keyof StancePair, val: string) => {
    const next = [...stancePairs];
    next[idx] = { ...next[idx], [field]: val };
    setStancePairs(next);
  };
  const addStance = () => setStancePairs([...stancePairs, { topic: '', stance: 'support' }]);
  const removeStance = (idx: number) => setStancePairs(stancePairs.filter((_, i) => i !== idx));

  const sliderRow = (label: string, key: keyof CommunicationStyle, lo: string, hi: string) => (
    <div className="slider-group">
      <label>{label}: <strong>{comm()[key] as number}</strong></label>
      <input
        type="range" min="1" max="10"
        value={comm()[key] as number}
        onChange={e => setComm(key, parseInt(e.target.value))}
      />
      <div className="slider-ends"><span>{lo}</span><span>{hi}</span></div>
    </div>
  );

  return (
    <div className="souls-context view-container">
      <div className="header-row">
        <div>
          <h1>Agent Souls</h1>
          <p className="subtitle">Visual Persona Builder — no raw JSON, fully validated.</p>
        </div>
        <button className="btn-primary" onClick={fetchProfiles}>Refresh</button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="grid">
        {loading ? <p>Loading profiles...</p> : profiles.map(p => (
          <div key={p.agent_id} className="card" onClick={() => openProfile(p)}>
            <h3>
              {p.full_name} <span className={`badge caste-${p.caste}`}>{p.caste}</span>
              <span className={`status-badge ${p.status}`} style={{ marginLeft: 6 }}>{p.status}</span>
            </h3>
            <p className="text-muted">{p.agent_id} / {p.codename}</p>
            <div className="platforms">
              {(p.platforms || []).map(pl => <span key={pl} className="tag">{pl}</span>)}
            </div>
          </div>
        ))}
      </div>

      {selectedProfile && (
        <div className="modal-overlay" onClick={() => setSelectedProfile(null)}>
          <div className="modal-content large" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Edit Profile: {selectedProfile.full_name} ({selectedProfile.agent_id})</h2>
              <div className="tabs">
                <button className={`tab-btn ${activeTab === 'identity' ? 'active' : ''}`} onClick={() => setActiveTab('identity')}>Identity</button>
                <button className={`tab-btn ${activeTab === 'psychology' ? 'active' : ''}`} onClick={() => setActiveTab('psychology')}>Psychology & Style</button>
                <button className={`tab-btn ${activeTab === 'mission' ? 'active' : ''}`} onClick={() => setActiveTab('mission')}>Mission & Stance</button>
                <button className={`tab-btn ${activeTab === 'binding' ? 'active' : ''}`} onClick={() => setActiveTab('binding')}>Bound Accounts</button>
                <button className={`tab-btn ${activeTab === 'history' ? 'active' : ''}`} onClick={() => setActiveTab('history')}>History & Rollback</button>
              </div>
            </div>

            <div className="modal-body">
              {saveError && <div className="error-banner">{saveError}</div>}

              {activeTab === 'identity' && (
                <div className="form-grid">
                  <div className="form-group">
                    <label>Full Name</label>
                    <input value={selectedProfile.full_name || ''} onChange={e => setSelectedProfile({ ...selectedProfile, full_name: e.target.value })} />
                  </div>
                  <div className="form-group">
                    <label>Codename</label>
                    <input value={selectedProfile.codename} onChange={e => setSelectedProfile({ ...selectedProfile, codename: e.target.value })} />
                  </div>
                  <div className="form-group">
                    <label>Residence City</label>
                    <input value={selectedProfile.residence_city || ''} onChange={e => setSelectedProfile({ ...selectedProfile, residence_city: e.target.value })} />
                  </div>
                  <div className="form-group">
                    <label>Profession</label>
                    <input value={selectedProfile.profession || ''} onChange={e => setSelectedProfile({ ...selectedProfile, profession: e.target.value })} />
                  </div>
                  <div className="form-group">
                    <label>Caste</label>
                    <div className="toggle-group">
                      {CASTES.map(c => (
                        <button key={c} className={selectedProfile.caste === c ? `active ${c}` : ''} onClick={() => setSelectedProfile({ ...selectedProfile, caste: c })}>
                          {c.toUpperCase()}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="form-group full-width">
                    <label>Platforms</label>
                    <div className="pill-group">
                      {PLATFORMS.map(pl => (
                        <button key={pl} className={`pill ${(selectedProfile.platforms || []).includes(pl) ? 'selected' : ''}`} onClick={() => togglePlatform(pl)}>
                          {pl}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="form-group row-flex">
                    <div>
                      <label>Active Hours Start (0-23)</label>
                      <input type="number" min="0" max="23" value={selectedProfile.active_hours_start} onChange={e => setSelectedProfile({ ...selectedProfile, active_hours_start: parseInt(e.target.value) })} />
                    </div>
                    <div>
                      <label>Active Hours End (0-23)</label>
                      <input type="number" min="0" max="23" value={selectedProfile.active_hours_end} onChange={e => setSelectedProfile({ ...selectedProfile, active_hours_end: parseInt(e.target.value) })} />
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'psychology' && (
                <div className="form-grid">
                  <div className="form-group full-width section">
                    <h3>Communication Sliders (1–10)</h3>
                    <div className="slider-row">
                      {sliderRow('Tone', 'tone_level', 'Formal', 'Casual')}
                      {sliderRow('Emoji Frequency', 'emoji_frequency', 'None', 'Heavy')}
                    </div>
                    <div className="slider-row mt-2">
                      {sliderRow('Vocabulary Level', 'vocab_level', 'Simple', 'Erudite')}
                      {sliderRow('Aggression', 'aggression', 'Passive', 'Combative')}
                    </div>
                  </div>

                  <div className="form-group full-width section">
                    <div className="array-header">
                      <h3>Typing / Speech Quirks</h3>
                      <button className="btn-secondary btn-add" onClick={addQuirk}>+ Add Quirk</button>
                    </div>
                    {comm().quirks.length === 0 && <p className="text-muted">No quirks defined.</p>}
                    {comm().quirks.map((q, i) => (
                      <div key={i} className="array-row">
                        <input value={q} placeholder="e.g. lowercase only, no periods" onChange={e => updateQuirk(i, e.target.value)} />
                        <button className="btn-icon text-danger" onClick={() => removeQuirk(i)}>✕</button>
                      </div>
                    ))}
                  </div>

                  <div className="form-group full-width section">
                    <div className="array-header">
                      <h3>Behavioral Rules</h3>
                      <button className="btn-secondary btn-add" onClick={addRule}>+ Add Rule</button>
                    </div>
                    {rules().rules.length === 0 && <p className="text-muted">No behavioral rules defined.</p>}
                    {rules().rules.map((r, i) => (
                      <div key={i} className="array-row">
                        <input value={r} placeholder="e.g. Never discuss politics directly" onChange={e => updateRule(i, e.target.value)} />
                        <button className="btn-icon text-danger" onClick={() => removeRule(i)}>✕</button>
                      </div>
                    ))}
                    <div className="row-flex mt-3">
                      <div>
                        <label>Min Delay Between Posts (sec)</label>
                        <input type="number" min="0" value={rules().min_delay_between_posts_sec} onChange={e => setRules('min_delay_between_posts_sec', parseInt(e.target.value) || 0)} />
                      </div>
                      <div>
                        <label>Max Posts Per Hour</label>
                        <input type="number" min="0" value={rules().max_posts_per_hour} onChange={e => setRules('max_posts_per_hour', parseInt(e.target.value) || 0)} />
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'mission' && (
                <div className="form-grid">
                  <div className="form-group full-width section">
                    <h3>Context Subscriptions (RAG)</h3>
                    <p className="help-text">Cognitive layers this agent may retrieve from MUNINN's memory when crafting replies. ORPHEUS injects only facts tagged with a subscribed layer.</p>
                    <div className="pill-group">
                      {SUBSCRIPTION_LAYERS.map(layer => (
                        <button
                          key={layer}
                          type="button"
                          className={`pill ${(selectedProfile.context_subscriptions || []).includes(layer) ? 'selected' : ''}`}
                          onClick={() => toggleSubscription(layer)}
                        >
                          {layer}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="form-group full-width">
                    <label>Core Mission</label>
                    <textarea
                      rows={5}
                      value={selectedProfile.core_mission || ''}
                      onChange={e => setSelectedProfile({ ...selectedProfile, core_mission: e.target.value })}
                      placeholder="Define the primary objective and narrative stance for this persona."
                    />
                    <p className="help-text">Free-text narrative directive (not JSON).</p>
                  </div>

                  <div className="form-group full-width section">
                    <div className="array-header">
                      <h3>Stance Modifiers</h3>
                      <button className="btn-secondary btn-add" onClick={addStance}>+ Add Stance</button>
                    </div>
                    <p className="help-text">Per-topic dynamic stance applied at runtime. Compiled into a validated object on save.</p>
                    {stancePairs.length === 0 && <p className="text-muted">No stance modifiers defined.</p>}
                    {stancePairs.map((pair, i) => (
                      <div key={i} className="array-row stance-row">
                        <input value={pair.topic} placeholder="Topic (e.g. local_elections)" onChange={e => updateStance(i, 'topic', e.target.value)} />
                        <select value={pair.stance} onChange={e => updateStance(i, 'stance', e.target.value)}>
                          <option value="support">support</option>
                          <option value="attack">attack</option>
                          <option value="neutral">neutral</option>
                          <option value="amplify">amplify</option>
                          <option value="undermine">undermine</option>
                        </select>
                        <button className="btn-icon text-danger" onClick={() => removeStance(i)}>✕</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {activeTab === 'binding' && (
                <div className="form-grid">
                  <div className="form-group full-width section">
                    <h3>Bound Accounts (Access Keys / Hardware)</h3>
                    <p className="help-text">Souls (psychology) and accounts (access) are decoupled. Bind floating accounts to this soul, or unbind them back to the pool.</p>

                    <div className="history-list mt-3">
                      {accounts.filter(a => a.agent_id === selectedProfile.agent_id).length === 0 ? (
                        <p className="text-muted">No accounts bound to this soul.</p>
                      ) : (
                        accounts.filter(a => a.agent_id === selectedProfile.agent_id).map(a => (
                          <div key={a.id} className="array-row" style={{ alignItems: 'center' }}>
                            <span><span className="tag">{a.platform}</span> {a.username} <span className={`status-badge ${a.status}`}>{a.status}</span></span>
                            <button className="btn-secondary" onClick={() => handleUnbindAccount(a.id)}>Unbind</button>
                          </div>
                        ))
                      )}
                    </div>

                    <div className="row-flex mt-3" style={{ alignItems: 'flex-end' }}>
                      <div style={{ flex: 1 }}>
                        <label>Bind a floating account</label>
                        <select value={bindAccountId} onChange={e => setBindAccountId(e.target.value)}>
                          <option value="">Select an unbound account…</option>
                          {accounts.filter(a => !a.agent_id).map(a => (
                            <option key={a.id} value={a.id}>{a.platform} · {a.username} ({a.status})</option>
                          ))}
                        </select>
                      </div>
                      <button
                        className="btn-primary"
                        disabled={!bindAccountId}
                        onClick={() => handleBindAccount(parseInt(bindAccountId), selectedProfile.agent_id)}
                      >
                        Bind to this Soul
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'history' && (
                <div className="form-grid">
                  <div className="form-group full-width">
                    <h3>Profile Audit History</h3>
                    <p className="help-text">View and revert to previous versions of this profile.</p>
                    <div className="history-list mt-4">
                      {historyLogs.length === 0 ? <p className="text-muted">No history found.</p> : (
                        <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
                          <thead>
                            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                              <th style={{ padding: '8px' }}>Date</th>
                              <th style={{ padding: '8px' }}>Preview</th>
                              <th style={{ padding: '8px' }}>Actions</th>
                            </tr>
                          </thead>
                          <tbody>
                            {historyLogs.map(log => (
                              <tr key={log.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                <td style={{ padding: '8px' }}>{new Date(log.created_at).toLocaleString()}</td>
                                <td style={{ padding: '8px', fontSize: '0.85em', color: '#888' }}>
                                  {log.profile_data.full_name} ({log.profile_data.caste})
                                </td>
                                <td style={{ padding: '8px' }}>
                                  <button className="btn-secondary" onClick={() => handleRollback(log.id)}>
                                    Rollback to this version
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="modal-actions">
              <button className="btn-secondary" onClick={() => setSelectedProfile(null)}>Cancel</button>
              <button className="btn-primary" onClick={handleSave}>Save Profile</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SoulsContext;
