import { useState, useEffect } from 'react';
import './SoulsContext.css';

interface CommunicationStyle {
  tone_level: number;
  emoji_frequency: number;
  vocab_level: number;
  aggression: number;
  [key: string]: unknown;
}

interface Profile {
  id: number;
  agent_id: string;
  codename: string;
  full_name: string;
  caste: string;
  profession: string | null;
  residence_city: string | null;
  platforms: string[];
  active_hours_start: number;
  active_hours_end: number;
  communication_style: CommunicationStyle;
  behavioral_rules: Record<string, any> | null;
  core_mission: string | null;
  current_stance_modifiers: Record<string, any> | null;
}

interface SoulsContextProps {
  token: string;
}

const PLATFORMS = ['telegram', 'instagram', 'youtube', 'threads', 'web'];
const CASTES = ['alpha', 'beta', 'gamma'];

const SoulsContext: React.FC<SoulsContextProps> = ({ token }) => {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  
  // Tab State
  const [activeTab, setActiveTab] = useState<'identity' | 'psychology' | 'mission' | 'history'>('identity');
  
  // History State
  const [historyLogs, setHistoryLogs] = useState<any[]>([]);
  
  // JSON Edit State
  const [commStyleJson, setCommStyleJson] = useState('');
  const [behavioralRulesJson, setBehavioralRulesJson] = useState('');
  const [stanceModifiersJson, setStanceModifiersJson] = useState('');
  const [jsonError, setJsonError] = useState('');

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  useEffect(() => {
    fetchProfiles();
  }, []);
  
  useEffect(() => {
    if (selectedProfile) {
      // Sync JSON strings when modal opens
      const { tone_level, emoji_frequency, vocab_level, aggression, ...baseComm } = selectedProfile.communication_style || {};
      setCommStyleJson(JSON.stringify(baseComm, null, 2));
      
      setBehavioralRulesJson(JSON.stringify(selectedProfile.behavioral_rules || {}, null, 2));
      setStanceModifiersJson(JSON.stringify(selectedProfile.current_stance_modifiers || {}, null, 2));
      setJsonError('');
      
      if (activeTab === 'history') {
        fetchHistory(selectedProfile.agent_id);
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
        headers
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

  const handleSave = async () => {
    if (!selectedProfile) return;
    
    // Parse JSONs
    let parsedCommExtra, parsedBehavioral, parsedStance;
    try {
      parsedCommExtra = JSON.parse(commStyleJson || '{}');
      parsedBehavioral = JSON.parse(behavioralRulesJson || '{}');
      parsedStance = JSON.parse(stanceModifiersJson || '{}');
    } catch (e) {
      setJsonError('Invalid JSON format. Please correct syntax errors before saving.');
      return;
    }
    
    const finalCommStyle = {
      ...parsedCommExtra,
      tone_level: selectedProfile.communication_style?.tone_level ?? 5,
      emoji_frequency: selectedProfile.communication_style?.emoji_frequency ?? 3,
      vocab_level: selectedProfile.communication_style?.vocab_level ?? 5,
      aggression: selectedProfile.communication_style?.aggression ?? 3,
    };
    
    const payload = {
      ...selectedProfile,
      communication_style: finalCommStyle,
      behavioral_rules: parsedBehavioral,
      current_stance_modifiers: parsedStance
    };

    try {
      const res = await fetch(`/api/v1/souls/profiles/${selectedProfile.agent_id}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`Failed to save: ${res.status}`);
      fetchProfiles();
      setSelectedProfile(null);
    } catch (e) {
      setJsonError(e instanceof Error ? e.message : 'Failed to save');
    }
  };

  const togglePlatform = (platform: string) => {
    if (!selectedProfile) return;
    const current = new Set(selectedProfile.platforms || []);
    if (current.has(platform)) current.delete(platform);
    else current.add(platform);
    setSelectedProfile({ ...selectedProfile, platforms: Array.from(current) });
  };

  const getCommStyle = (): CommunicationStyle => {
    return selectedProfile?.communication_style || { tone_level: 5, emoji_frequency: 3, vocab_level: 5, aggression: 3 };
  };

  const setCommStyle = (key: string, val: number) => {
    if (!selectedProfile) return;
    setSelectedProfile({
      ...selectedProfile,
      communication_style: { ...getCommStyle(), [key]: val },
    });
  };

  return (
    <div className="souls-context view-container">
      <div className="header-row">
        <div>
          <h1>Agent Souls</h1>
          <p className="subtitle">Manage psychological profiles and narrative stances.</p>
        </div>
        <button className="btn-primary" onClick={fetchProfiles}>Refresh</button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="grid">
        {loading ? <p>Loading profiles...</p> : profiles.map(p => (
          <div key={p.agent_id} className="card" onClick={() => setSelectedProfile({...p})}>
            <h3>{p.full_name} <span className={`badge caste-${p.caste}`}>{p.caste}</span></h3>
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
                <button 
                  className={`tab-btn ${activeTab === 'identity' ? 'active' : ''}`} 
                  onClick={() => setActiveTab('identity')}
                >Identity</button>
                <button 
                  className={`tab-btn ${activeTab === 'psychology' ? 'active' : ''}`} 
                  onClick={() => setActiveTab('psychology')}
                >Psychology & Style</button>
                <button 
                  className={`tab-btn ${activeTab === 'mission' ? 'active' : ''}`} 
                  onClick={() => setActiveTab('mission')}
                >Mission & Stance</button>
                <button 
                  className={`tab-btn ${activeTab === 'history' ? 'active' : ''}`} 
                  onClick={() => setActiveTab('history')}
                >History & Rollback</button>
              </div>
            </div>

            <div className="modal-body">
              {jsonError && <div className="error-banner">{jsonError}</div>}
              
              {activeTab === 'identity' && (
                <div className="form-grid">
                  <div className="form-group">
                    <label>Full Name</label>
                    <input
                      value={selectedProfile.full_name || ''}
                      onChange={e => setSelectedProfile({...selectedProfile, full_name: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Codename</label>
                    <input
                      value={selectedProfile.codename}
                      onChange={e => setSelectedProfile({...selectedProfile, codename: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Residence City</label>
                    <input
                      value={selectedProfile.residence_city || ''}
                      onChange={e => setSelectedProfile({...selectedProfile, residence_city: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Profession</label>
                    <input
                      value={selectedProfile.profession || ''}
                      onChange={e => setSelectedProfile({...selectedProfile, profession: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Caste</label>
                    <div className="toggle-group">
                      {CASTES.map(c => (
                        <button
                          key={c}
                          className={selectedProfile.caste === c ? `active ${c}` : ''}
                          onClick={() => setSelectedProfile({...selectedProfile, caste: c})}
                        >
                          {c.toUpperCase()}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="form-group full-width">
                    <label>Platforms</label>
                    <div className="pill-group">
                      {PLATFORMS.map(pl => (
                        <button
                          key={pl}
                          className={`pill ${(selectedProfile.platforms || []).includes(pl) ? 'selected' : ''}`}
                          onClick={() => togglePlatform(pl)}
                        >
                          {pl}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="form-group row-flex">
                    <div>
                      <label>Active Hours Start (0-23)</label>
                      <input 
                        type="number" min="0" max="23"
                        value={selectedProfile.active_hours_start}
                        onChange={e => setSelectedProfile({...selectedProfile, active_hours_start: parseInt(e.target.value)})}
                      />
                    </div>
                    <div>
                      <label>Active Hours End (0-23)</label>
                      <input 
                        type="number" min="0" max="23"
                        value={selectedProfile.active_hours_end}
                        onChange={e => setSelectedProfile({...selectedProfile, active_hours_end: parseInt(e.target.value)})}
                      />
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'psychology' && (
                <div className="form-grid">
                  <div className="form-group full-width section">
                    <h3>Base Sliders</h3>
                    <div className="slider-row">
                      <div className="slider-group">
                        <label>Tone (Formal ← → Casual): {getCommStyle().tone_level ?? 5}</label>
                        <input type="range" min="1" max="10" value={getCommStyle().tone_level ?? 5}
                          onChange={e => setCommStyle('tone_level', parseInt(e.target.value))}
                        />
                      </div>
                      <div className="slider-group">
                        <label>Emoji Frequency: {getCommStyle().emoji_frequency ?? 3}</label>
                        <input type="range" min="1" max="10" value={getCommStyle().emoji_frequency ?? 3}
                          onChange={e => setCommStyle('emoji_frequency', parseInt(e.target.value))}
                        />
                      </div>
                    </div>
                    <div className="slider-row mt-2">
                      <div className="slider-group">
                        <label>Vocabulary Level: {getCommStyle().vocab_level ?? 5}</label>
                        <input type="range" min="1" max="10" value={getCommStyle().vocab_level ?? 5}
                          onChange={e => setCommStyle('vocab_level', parseInt(e.target.value))}
                        />
                      </div>
                      <div className="slider-group">
                        <label>Aggression: {getCommStyle().aggression ?? 3}</label>
                        <input type="range" min="1" max="10" value={getCommStyle().aggression ?? 3}
                          onChange={e => setCommStyle('aggression', parseInt(e.target.value))}
                        />
                      </div>
                    </div>
                  </div>
                  
                  <div className="form-group full-width">
                    <label>Additional Communication Style (JSON)</label>
                    <textarea 
                      className="json-textarea"
                      rows={4}
                      value={commStyleJson}
                      onChange={e => setCommStyleJson(e.target.value)}
                    />
                  </div>
                  
                  <div className="form-group full-width">
                    <label>Behavioral Rules (JSON)</label>
                    <textarea 
                      className="json-textarea"
                      rows={6}
                      value={behavioralRulesJson}
                      onChange={e => setBehavioralRulesJson(e.target.value)}
                    />
                  </div>
                </div>
              )}

              {activeTab === 'mission' && (
                <div className="form-grid">
                  <div className="form-group full-width">
                    <label>Core Mission</label>
                    <textarea
                      rows={5}
                      value={selectedProfile.core_mission || ''}
                      onChange={e => setSelectedProfile({...selectedProfile, core_mission: e.target.value})}
                      placeholder="Define the primary objective and narrative stance for this persona."
                    />
                  </div>
                  
                  <div className="form-group full-width">
                    <label>Current Stance Modifiers (JSON)</label>
                    <textarea 
                      className="json-textarea"
                      rows={8}
                      value={stanceModifiersJson}
                      onChange={e => setStanceModifiersJson(e.target.value)}
                      placeholder='{"topic_x": "support", "topic_y": "attack"}'
                    />
                    <p className="help-text">Dynamic modifiers applied to the Core Mission during runtime.</p>
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
