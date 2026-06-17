import { useState, useEffect, useCallback } from 'react';
import './SoulsContext.css';
import ChannelManager from './ChannelManager';
import { SidePanel } from './SidePanel';

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

interface LiveStatus {
  agent_id: string;
  event: string;
  detail: string;
  status: string;
  ts: string;
}

interface SoulsContextProps {
  token: string;
}

const PLATFORMS = ['telegram', 'instagram', 'youtube', 'threads', 'web'];
const CASTES = ['alpha', 'beta', 'gamma'];
const SUBSCRIPTION_LAYERS = ['global', 'regional', 'state', 'city', 'personal'];

const STATUS_LABEL: Record<string, string> = {
  active: 'активен',
  suspended: 'на паузе',
  unbound: 'без аккаунта',
  banned: 'забанен',
  limited: 'ограничен',
};

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

// Is the live status fresh & in-flight?
function liveness(s: LiveStatus | undefined): 'working' | 'idle' | 'asleep' {
  if (!s) return 'asleep';
  const ageMs = Date.now() - parseFloat(s.ts) * 1000;
  if (s.status === 'active' && ageMs < 90_000) return 'working';
  if (ageMs < 300_000) return 'idle';
  return 'asleep';
}

const SoulsContext: React.FC<SoulsContextProps> = ({ token }) => {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState('');

  const [activeTab, setActiveTab] = useState<'identity' | 'psychology' | 'mission' | 'binding' | 'history'>('identity');
  const [historyLogs, setHistoryLogs] = useState<any[]>([]);

  const [accounts, setAccounts] = useState<Account[]>([]);
  const [bindAccountId, setBindAccountId] = useState('');
  const [stancePairs, setStancePairs] = useState<StancePair[]>([]);

  // Live status per agent (from the Live Ops telemetry stream) + filters.
  const [live, setLive] = useState<Record<string, LiveStatus>>({});
  const [query, setQuery] = useState('');
  const [filterCaste, setFilterCaste] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [channelAgent, setChannelAgent] = useState<{ agentId: string; label: string } | null>(null);

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/v1/souls/profiles', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProfiles(Array.isArray(data) ? data : []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить профили');
      setProfiles([]);
    }
    setLoading(false);
  }, [token]);

  const fetchAccounts = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/souls/accounts', { headers });
      if (res.ok) setAccounts(await res.json());
    } catch (e) {
      console.error('Не удалось загрузить аккаунты', e);
    }
  }, [token]);

  const fetchLive = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/analytics/live?limit=1', { headers });
      if (!res.ok) return;
      const data = await res.json();
      const map: Record<string, LiveStatus> = {};
      for (const a of data.agents || []) map[a.agent_id] = a;
      setLive(map);
    } catch { /* live status is best-effort */ }
  }, [token]);

  useEffect(() => {
    fetchProfiles();
    fetchAccounts();
    fetchLive();
    const iv = setInterval(fetchLive, 3000);
    return () => clearInterval(iv);
  }, [fetchProfiles, fetchAccounts, fetchLive]);

  useEffect(() => {
    if (selectedProfile) {
      const stance = selectedProfile.current_stance_modifiers || {};
      setStancePairs(Object.entries(stance).map(([topic, s]) => ({ topic, stance: String(s) })));
      setSaveError('');
      if (activeTab === 'history') fetchHistory(selectedProfile.agent_id);
      if (activeTab === 'binding') { fetchAccounts(); setBindAccountId(''); }
    }
  }, [selectedProfile?.agent_id, activeTab]);

  const fetchHistory = async (agentId: string) => {
    try {
      const res = await fetch(`/api/v1/souls/profiles/${agentId}/history`, { headers });
      if (res.ok) setHistoryLogs(await res.json());
    } catch (e) { console.error('Не удалось загрузить историю', e); }
  };

  const handleRollback = async (historyId: number) => {
    if (!selectedProfile) return;
    if (!confirm('Откатить профиль к этой версии?')) return;
    try {
      const res = await fetch(`/api/v1/souls/profiles/${selectedProfile.agent_id}/rollback/${historyId}`, { method: 'POST', headers });
      if (res.ok) { fetchProfiles(); setSelectedProfile(null); }
    } catch (e) { console.error(e); }
  };

  // Pause / resume an agent (active ↔ suspended).
  const setAgentStatus = async (agentId: string, status: 'active' | 'suspended') => {
    try {
      const res = await fetch(`/api/v1/souls/profiles/${agentId}/status`, {
        method: 'POST', headers, body: JSON.stringify({ status }),
      });
      if (res.ok) {
        setProfiles(prev => prev.map(p => p.agent_id === agentId ? { ...p, status } : p));
        if (selectedProfile?.agent_id === agentId) setSelectedProfile({ ...selectedProfile, status });
      }
    } catch (e) { console.error(e); }
  };

  const handleBindAccount = async (accountId: number, agentId: string) => {
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/bind?agent_id=${encodeURIComponent(agentId)}`, { method: 'PUT', headers });
      if (res.ok) { await fetchAccounts(); fetchProfiles(); setBindAccountId(''); }
    } catch (e) { console.error(e); }
  };

  const handleUnbindAccount = async (accountId: number) => {
    try {
      const res = await fetch(`/api/v1/souls/accounts/${accountId}/unbind`, { method: 'PUT', headers });
      if (res.ok) { await fetchAccounts(); fetchProfiles(); }
    } catch (e) { console.error(e); }
  };

  const openProfile = (p: Profile) => {
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
        method: 'PUT', headers, body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : `Не удалось сохранить: ${res.status}`);
      }
      fetchProfiles();
      setSelectedProfile(null);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Не удалось сохранить');
    }
  };

  const togglePlatform = (platform: string) => {
    if (!selectedProfile) return;
    const current = new Set(selectedProfile.platforms || []);
    current.has(platform) ? current.delete(platform) : current.add(platform);
    setSelectedProfile({ ...selectedProfile, platforms: Array.from(current) });
  };

  const toggleSubscription = (layer: string) => {
    if (!selectedProfile) return;
    const current = new Set(selectedProfile.context_subscriptions || ['global']);
    current.has(layer) ? current.delete(layer) : current.add(layer);
    setSelectedProfile({ ...selectedProfile, context_subscriptions: Array.from(current) });
  };

  const comm = (): CommunicationStyle => normalizeComm(selectedProfile?.communication_style);
  const setComm = (key: keyof CommunicationStyle, val: any) => {
    if (!selectedProfile) return;
    setSelectedProfile({ ...selectedProfile, communication_style: { ...comm(), [key]: val } });
  };
  const updateQuirk = (idx: number, val: string) => { const next = [...comm().quirks]; next[idx] = val; setComm('quirks', next); };
  const addQuirk = () => setComm('quirks', [...comm().quirks, '']);
  const removeQuirk = (idx: number) => setComm('quirks', comm().quirks.filter((_, i) => i !== idx));

  const rules = (): BehavioralRules => normalizeRules(selectedProfile?.behavioral_rules);
  const setRules = (key: keyof BehavioralRules, val: any) => {
    if (!selectedProfile) return;
    setSelectedProfile({ ...selectedProfile, behavioral_rules: { ...rules(), [key]: val } });
  };
  const updateRule = (idx: number, val: string) => { const next = [...rules().rules]; next[idx] = val; setRules('rules', next); };
  const addRule = () => setRules('rules', [...rules().rules, '']);
  const removeRule = (idx: number) => setRules('rules', rules().rules.filter((_, i) => i !== idx));

  const updateStance = (idx: number, field: keyof StancePair, val: string) => {
    const next = [...stancePairs]; next[idx] = { ...next[idx], [field]: val }; setStancePairs(next);
  };
  const addStance = () => setStancePairs([...stancePairs, { topic: '', stance: 'support' }]);
  const removeStance = (idx: number) => setStancePairs(stancePairs.filter((_, i) => i !== idx));

  const sliderRow = (label: string, key: keyof CommunicationStyle, lo: string, hi: string) => (
    <div className="slider-group">
      <label>{label}: <strong>{comm()[key] as number}</strong></label>
      <input type="range" min="1" max="10" value={comm()[key] as number} onChange={e => setComm(key, parseInt(e.target.value))} />
      <div className="slider-ends"><span>{lo}</span><span>{hi}</span></div>
    </div>
  );

  // ── Filtering ──
  const visible = profiles.filter(p => {
    if (filterCaste !== 'all' && p.caste !== filterCaste) return false;
    if (filterStatus !== 'all' && p.status !== filterStatus) return false;
    if (query.trim()) {
      const q = query.toLowerCase();
      if (!(`${p.full_name} ${p.codename} ${p.agent_id} ${p.profession || ''}`.toLowerCase().includes(q))) return false;
    }
    return true;
  });

  const boundAccountsFor = (agentId: string) => accounts.filter(a => a.agent_id === agentId);

  return (
    <div className="souls-context view-container">
      <div className="header-row">
        <div>
          <h1>Агенты</h1>
          <p className="subtitle">Управление ботами: персона, поведение, запуск/пауза.</p>
        </div>
        <button className="btn-primary" onClick={fetchProfiles}>Обновить</button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* Toolbar: search + filters */}
      <div className="sc-toolbar">
        <input
          className="sc-search"
          placeholder="🔍 Поиск по имени, codename, agent_id…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <div className="sc-filters">
          <span className="sc-filter-label">Каста:</span>
          <button className={`pill ${filterCaste === 'all' ? 'selected' : ''}`} onClick={() => setFilterCaste('all')}>все</button>
          {CASTES.map(c => (
            <button key={c} className={`pill ${filterCaste === c ? 'selected' : ''}`} onClick={() => setFilterCaste(c)}>{c}</button>
          ))}
          <span className="sc-filter-label" style={{ marginLeft: 12 }}>Статус:</span>
          {['all', 'active', 'suspended'].map(s => (
            <button key={s} className={`pill ${filterStatus === s ? 'selected' : ''}`} onClick={() => setFilterStatus(s)}>
              {s === 'all' ? 'все' : STATUS_LABEL[s] || s}
            </button>
          ))}
        </div>
      </div>

      <div className="sc-grid">
        {loading ? <p>Загрузка профилей…</p> : visible.length === 0 ? (
          <p className="text-muted">Ничего не найдено.</p>
        ) : visible.map(p => {
          const ls = live[p.agent_id];
          const lv = liveness(ls);
          const bound = boundAccountsFor(p.agent_id);
          const paused = p.status === 'suspended';
          return (
            <div key={p.agent_id} className={`sc-card ${paused ? 'paused' : ''}`}>
              <div className="sc-card-top" onClick={() => openProfile(p)}>
                <div className="sc-card-title">
                  <span className={`sc-live-dot ${lv}`} title={lv === 'working' ? 'работает сейчас' : lv === 'idle' ? 'недавно активен' : 'не активен'} />
                  <strong>{p.full_name || p.agent_id}</strong>
                  <span className={`badge caste-${p.caste}`}>{p.caste}</span>
                </div>
                <span className={`status-badge ${p.status}`}>{STATUS_LABEL[p.status] || p.status}</span>
              </div>

              <p className="sc-card-sub" onClick={() => openProfile(p)}>
                <span className="font-mono">{p.agent_id}</span> · {p.codename}
                {p.profession ? ` · ${p.profession}` : ''}
              </p>

              <div className="sc-card-now" onClick={() => openProfile(p)}>
                {ls ? <>🛈 {ls.detail || ls.event}</> : <span className="text-muted">нет недавней активности</span>}
              </div>

              <div className="sc-card-meta" onClick={() => openProfile(p)}>
                <div className="platforms">{(p.platforms || []).map(pl => <span key={pl} className="tag">{pl}</span>)}</div>
                <div className="sc-card-accounts">
                  {bound.length === 0
                    ? <span className="text-muted">аккаунт не привязан</span>
                    : bound.map(a => <span key={a.id} className="tag tag-acct">{a.platform}:{a.username}</span>)}
                </div>
              </div>

              <div className="sc-card-actions">
                {paused ? (
                  <button className="sc-btn-resume" onClick={() => setAgentStatus(p.agent_id, 'active')}>▶ Запустить</button>
                ) : (
                  <button className="sc-btn-pause" onClick={() => setAgentStatus(p.agent_id, 'suspended')}>⏸ Пауза</button>
                )}
                <button className="btn-secondary" onClick={() => openProfile(p)}>✎ Редактировать</button>
              </div>

              {bound.some(a => a.platform === 'telegram') && (
                <button className="sc-btn-channels" onClick={() => setChannelAgent({ agentId: p.agent_id, label: p.full_name || p.agent_id })}>
                  📡 Каналы аккаунта
                </button>
              )}
            </div>
          );
        })}
      </div>

      {channelAgent && (
        <ChannelManager token={token} agentId={channelAgent.agentId} label={channelAgent.label} onClose={() => setChannelAgent(null)} />
      )}

      {selectedProfile && (
        <SidePanel
          open
          title={`Редактирование: ${selectedProfile.full_name} (${selectedProfile.agent_id})`}
          onClose={() => setSelectedProfile(null)}
          width={680}
          footer={
            <>
              <button className="btn-secondary" onClick={() => setSelectedProfile(null)}>Отмена</button>
              <button className="btn-primary" onClick={handleSave}>Сохранить</button>
            </>
          }
        >
            <div className="sc-detail-bar">
              {selectedProfile.status === 'suspended'
                ? <button className="sc-btn-resume sc-hdr-btn" onClick={() => setAgentStatus(selectedProfile.agent_id, 'active')}>▶ Запустить</button>
                : <button className="sc-btn-pause sc-hdr-btn" onClick={() => setAgentStatus(selectedProfile.agent_id, 'suspended')}>⏸ Пауза</button>}
              <div className="tabs">
                <button className={`tab-btn ${activeTab === 'identity' ? 'active' : ''}`} onClick={() => setActiveTab('identity')}>Личность</button>
                <button className={`tab-btn ${activeTab === 'psychology' ? 'active' : ''}`} onClick={() => setActiveTab('psychology')}>Психология и стиль</button>
                <button className={`tab-btn ${activeTab === 'mission' ? 'active' : ''}`} onClick={() => setActiveTab('mission')}>Миссия и позиция</button>
                <button className={`tab-btn ${activeTab === 'binding' ? 'active' : ''}`} onClick={() => setActiveTab('binding')}>Аккаунты</button>
                <button className={`tab-btn ${activeTab === 'history' ? 'active' : ''}`} onClick={() => setActiveTab('history')}>История</button>
              </div>
            </div>

            <div className="sc-detail-body">
              {saveError && <div className="error-banner">{saveError}</div>}

              {activeTab === 'identity' && (
                <div className="form-grid">
                  <div className="form-group"><label>Полное имя</label>
                    <input value={selectedProfile.full_name || ''} onChange={e => setSelectedProfile({ ...selectedProfile, full_name: e.target.value })} /></div>
                  <div className="form-group"><label>Codename</label>
                    <input value={selectedProfile.codename} onChange={e => setSelectedProfile({ ...selectedProfile, codename: e.target.value })} /></div>
                  <div className="form-group"><label>Город проживания</label>
                    <input value={selectedProfile.residence_city || ''} onChange={e => setSelectedProfile({ ...selectedProfile, residence_city: e.target.value })} /></div>
                  <div className="form-group"><label>Профессия</label>
                    <input value={selectedProfile.profession || ''} onChange={e => setSelectedProfile({ ...selectedProfile, profession: e.target.value })} /></div>
                  <div className="form-group"><label>Каста</label>
                    <div className="toggle-group">
                      {CASTES.map(c => (
                        <button key={c} className={selectedProfile.caste === c ? `active ${c}` : ''} onClick={() => setSelectedProfile({ ...selectedProfile, caste: c })}>{c.toUpperCase()}</button>
                      ))}
                    </div>
                  </div>
                  <div className="form-group full-width"><label>Платформы</label>
                    <div className="pill-group">
                      {PLATFORMS.map(pl => (
                        <button key={pl} className={`pill ${(selectedProfile.platforms || []).includes(pl) ? 'selected' : ''}`} onClick={() => togglePlatform(pl)}>{pl}</button>
                      ))}
                    </div>
                  </div>
                  <div className="form-group row-flex">
                    <div><label>Активность с (0–23)</label>
                      <input type="number" min="0" max="23" value={selectedProfile.active_hours_start} onChange={e => setSelectedProfile({ ...selectedProfile, active_hours_start: parseInt(e.target.value) })} /></div>
                    <div><label>Активность до (0–23)</label>
                      <input type="number" min="0" max="23" value={selectedProfile.active_hours_end} onChange={e => setSelectedProfile({ ...selectedProfile, active_hours_end: parseInt(e.target.value) })} /></div>
                  </div>
                </div>
              )}

              {activeTab === 'psychology' && (
                <div className="form-grid">
                  <div className="form-group full-width section">
                    <h3>Стиль общения (1–10)</h3>
                    <div className="slider-row">
                      {sliderRow('Тон', 'tone_level', 'формальный', 'неформальный')}
                      {sliderRow('Частота эмодзи', 'emoji_frequency', 'нет', 'много')}
                    </div>
                    <div className="slider-row mt-2">
                      {sliderRow('Словарь', 'vocab_level', 'простой', 'эрудит')}
                      {sliderRow('Агрессивность', 'aggression', 'пассивный', 'задиристый')}
                    </div>
                  </div>

                  <div className="form-group full-width section">
                    <div className="array-header"><h3>Речевые причуды</h3>
                      <button className="btn-secondary btn-add" onClick={addQuirk}>+ Добавить</button></div>
                    {comm().quirks.length === 0 && <p className="text-muted">Причуды не заданы.</p>}
                    {comm().quirks.map((q, i) => (
                      <div key={i} className="array-row">
                        <input value={q} placeholder="напр. только строчные буквы, без точек" onChange={e => updateQuirk(i, e.target.value)} />
                        <button className="btn-icon text-danger" onClick={() => removeQuirk(i)}>✕</button>
                      </div>
                    ))}
                  </div>

                  <div className="form-group full-width section">
                    <div className="array-header"><h3>Правила поведения</h3>
                      <button className="btn-secondary btn-add" onClick={addRule}>+ Добавить</button></div>
                    {rules().rules.length === 0 && <p className="text-muted">Правила не заданы.</p>}
                    {rules().rules.map((r, i) => (
                      <div key={i} className="array-row">
                        <input value={r} placeholder="напр. никогда не обсуждать политику напрямую" onChange={e => updateRule(i, e.target.value)} />
                        <button className="btn-icon text-danger" onClick={() => removeRule(i)}>✕</button>
                      </div>
                    ))}
                    <div className="row-flex mt-3">
                      <div><label>Мин. пауза между постами (сек)</label>
                        <input type="number" min="0" value={rules().min_delay_between_posts_sec} onChange={e => setRules('min_delay_between_posts_sec', parseInt(e.target.value) || 0)} /></div>
                      <div><label>Макс. постов в час</label>
                        <input type="number" min="0" value={rules().max_posts_per_hour} onChange={e => setRules('max_posts_per_hour', parseInt(e.target.value) || 0)} /></div>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'mission' && (
                <div className="form-grid">
                  <div className="form-group full-width section">
                    <h3>Подписки на контекст (RAG)</h3>
                    <p className="help-text">Слои знаний, из которых агент берёт факты (MUNINN) при ответе. ORPHEUS подмешивает только факты подписанных слоёв.</p>
                    <div className="pill-group">
                      {SUBSCRIPTION_LAYERS.map(layer => (
                        <button key={layer} type="button" className={`pill ${(selectedProfile.context_subscriptions || []).includes(layer) ? 'selected' : ''}`} onClick={() => toggleSubscription(layer)}>{layer}</button>
                      ))}
                    </div>
                  </div>
                  <div className="form-group full-width">
                    <label>Главная миссия</label>
                    <textarea rows={5} value={selectedProfile.core_mission || ''} onChange={e => setSelectedProfile({ ...selectedProfile, core_mission: e.target.value })} placeholder="Опишите основную цель и нарративную позицию персоны." />
                    <p className="help-text">Свободный текст-директива (не JSON).</p>
                  </div>
                  <div className="form-group full-width section">
                    <div className="array-header"><h3>Модификаторы позиции</h3>
                      <button className="btn-secondary btn-add" onClick={addStance}>+ Добавить</button></div>
                    <p className="help-text">Позиция по темам, применяется в рантайме.</p>
                    {stancePairs.length === 0 && <p className="text-muted">Не заданы.</p>}
                    {stancePairs.map((pair, i) => (
                      <div key={i} className="array-row stance-row">
                        <input value={pair.topic} placeholder="Тема (напр. местные выборы)" onChange={e => updateStance(i, 'topic', e.target.value)} />
                        <select value={pair.stance} onChange={e => updateStance(i, 'stance', e.target.value)}>
                          <option value="support">поддержка</option>
                          <option value="attack">атака</option>
                          <option value="neutral">нейтрально</option>
                          <option value="amplify">усиление</option>
                          <option value="undermine">подрыв</option>
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
                    <h3>Привязанные аккаунты (доступы / железо)</h3>
                    <p className="help-text">Персона и аккаунты разделены. Привяжите свободный аккаунт к этой персоне или отвяжите обратно в пул.</p>
                    <div className="history-list mt-3">
                      {accounts.filter(a => a.agent_id === selectedProfile.agent_id).length === 0 ? (
                        <p className="text-muted">К этой персоне аккаунты не привязаны.</p>
                      ) : (
                        accounts.filter(a => a.agent_id === selectedProfile.agent_id).map(a => (
                          <div key={a.id} className="array-row" style={{ alignItems: 'center' }}>
                            <span><span className="tag">{a.platform}</span> {a.username} <span className={`status-badge ${a.status}`}>{a.status}</span></span>
                            <button className="btn-secondary" onClick={() => handleUnbindAccount(a.id)}>Отвязать</button>
                          </div>
                        ))
                      )}
                    </div>
                    <div className="row-flex mt-3" style={{ alignItems: 'flex-end' }}>
                      <div style={{ flex: 1 }}>
                        <label>Привязать свободный аккаунт</label>
                        <select value={bindAccountId} onChange={e => setBindAccountId(e.target.value)}>
                          <option value="">Выберите свободный аккаунт…</option>
                          {accounts.filter(a => !a.agent_id).map(a => (
                            <option key={a.id} value={a.id}>{a.platform} · {a.username} ({a.status})</option>
                          ))}
                        </select>
                      </div>
                      <button className="btn-primary" disabled={!bindAccountId} onClick={() => handleBindAccount(parseInt(bindAccountId), selectedProfile.agent_id)}>Привязать</button>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'history' && (
                <div className="form-grid">
                  <div className="form-group full-width">
                    <h3>История изменений профиля</h3>
                    <p className="help-text">Просмотр и откат к предыдущим версиям.</p>
                    <div className="history-list mt-4">
                      {historyLogs.length === 0 ? <p className="text-muted">История пуста.</p> : (
                        <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
                          <thead>
                            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                              <th style={{ padding: '8px' }}>Дата</th>
                              <th style={{ padding: '8px' }}>Версия</th>
                              <th style={{ padding: '8px' }}>Действие</th>
                            </tr>
                          </thead>
                          <tbody>
                            {historyLogs.map(log => (
                              <tr key={log.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                <td style={{ padding: '8px' }}>{new Date(log.created_at).toLocaleString('ru-RU')}</td>
                                <td style={{ padding: '8px', fontSize: '0.85em', color: '#888' }}>{log.profile_data.full_name} ({log.profile_data.caste})</td>
                                <td style={{ padding: '8px' }}>
                                  <button className="btn-secondary" onClick={() => handleRollback(log.id)}>Откатить сюда</button>
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
        </SidePanel>
      )}
    </div>
  );
};

export default SoulsContext;
