import { useState, useEffect, useCallback } from 'react';
import { SidePanel } from './SidePanel';
import './MissionDeck.css';

interface SquadMember { id: number; agent_id: string; assigned_role: string; status: string; codename?: string | null; }
interface Target { id: number; kind: string; identifier: string; title: string | null; status: string; source: string; proposed_by: string | null; reason: string | null; }
interface Mission {
  id: number; title: string; platform: string; narrative_goal: string | null; stance: string | null;
  tactic: string; status: string; agent_mode: string; dynamic_count: number; forced_context: string | null;
  squad: SquadMember[]; targets: Target[];
  summary: { status_label: string; agents: Record<string, number>; targets: Record<string, number> };
}
interface EligibleAgent {
  agent_id: string; codename: string | null; caste: string; status: string; active_mission_load: number;
  at_capacity: boolean; already_enlisted: boolean; match_score: number; match_reasons: string[];
}

interface MissionDeckProps {
  token: string;
  prefill?: { target_url: string; title: string; narrative_goal: string } | null;
  onPrefillConsumed?: () => void;
}

const TACTICS = [
  { v: 'dynamic', l: 'Динамическая (по ситуации)' },
  { v: 'soft_support', l: 'Мягкая поддержка' },
  { v: 'aggressive_displacement', l: 'Жёсткое вытеснение' },
];
const ROLE_COLOR: Record<string, string> = { alpha: '#ef4444', beta: '#3b82f6', gamma: '#22c55e' };
const inferKind = (id: string) => (/\/\d+\/?$/.test(id) ? 'post' : 'channel');

const MissionDeck: React.FC<MissionDeckProps> = ({ token, prefill, onPrefillConsumed }) => {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Mission | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const [cTitle, setCTitle] = useState('');
  const [cGoal, setCGoal] = useState('');
  const [cStance, setCStance] = useState('');
  const [cTactic, setCTactic] = useState('dynamic');
  const [cMode, setCMode] = useState('manual');
  const [cCount, setCCount] = useState(3);
  const [cTargets, setCTargets] = useState('');

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const fetchMissions = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/missions', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMissions(await res.json());
      setError('');
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Не удалось загрузить миссии'); }
  }, [token]);

  useEffect(() => { fetchMissions(); }, [fetchMissions]);

  useEffect(() => {
    if (prefill) {
      setCTitle(prefill.title || '');
      setCGoal(prefill.narrative_goal || '');
      setCTargets(prefill.target_url || '');
      setShowCreate(true);
      onPrefillConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  const createMission = async () => {
    const targets = cTargets.split(/[\n,]+/).map(s => s.trim()).filter(Boolean)
      .map(id => ({ identifier: id, kind: inferKind(id) }));
    try {
      const res = await fetch('/api/v1/missions', {
        method: 'POST', headers,
        body: JSON.stringify({ title: cTitle, narrative_goal: cGoal, stance: cStance, tactic: cTactic,
          agent_mode: cMode, dynamic_count: cCount, targets }),
      });
      if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail || `HTTP ${res.status}`); }
      setShowCreate(false);
      setCTitle(''); setCGoal(''); setCStance(''); setCTactic('dynamic'); setCMode('manual'); setCCount(3); setCTargets('');
      fetchMissions();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Не удалось создать миссию'); }
  };

  const refreshSelected = (m: Mission) => { setSelected(m); setMissions(prev => prev.map(x => x.id === m.id ? m : x)); };

  const setStatus = async (m: Mission, status: string) => {
    const res = await fetch(`/api/v1/missions/${m.id}/status`, { method: 'POST', headers, body: JSON.stringify({ status }) });
    if (res.ok) refreshSelected(await res.json());
  };

  const removeMission = async (m: Mission) => {
    if (!confirm(`Удалить миссию «${m.title}»?`)) return;
    const res = await fetch(`/api/v1/missions/${m.id}`, { method: 'DELETE', headers });
    if (res.ok) { setSelected(null); fetchMissions(); }
  };

  return (
    <div className="mission-deck view-container">
      <div className="header-row">
        <div>
          <h1>Миссии</h1>
          <p className="subtitle">Постоянные цели роя: своя «правда», цели и агенты. Миссия не завершается — только пауза.</p>
        </div>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>+ Новая миссия</button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="md-grid">
        {missions.length === 0 ? <p className="text-muted">Миссий пока нет.</p> : missions.map(m => {
          const sug = m.summary.targets.suggested || 0;
          return (
            <div key={m.id} className={`md-card ${m.status === 'paused' ? 'paused' : ''}`} onClick={() => setSelected(m)}>
              <div className="md-card-top">
                <strong>{m.title}</strong>
                <span className={`status-badge ${m.status === 'active' ? 'active' : 'suspended'}`}>{m.summary.status_label}</span>
              </div>
              {m.narrative_goal && <p className="md-goal">🎯 {m.narrative_goal}</p>}
              {m.stance && <p className="md-stance">⚖ {m.stance}</p>}
              <div className="md-card-meta">
                <span>👥 {m.summary.agents.total} агент(ов)</span>
                <span>🎯 {m.summary.targets.active || 0} цел.</span>
                {sug > 0 && <span className="md-sug-badge">⏳ {sug} предложено</span>}
              </div>
            </div>
          );
        })}
      </div>

      <SidePanel
        open={showCreate}
        title="Новая миссия"
        onClose={() => setShowCreate(false)}
        footer={
          <>
            <button className="btn-secondary" onClick={() => setShowCreate(false)}>Отмена</button>
            <button className="btn-primary" disabled={!cTitle.trim()} onClick={createMission}>Создать</button>
          </>
        }
      >
            <div className="form-group"><label>Название</label>
              <input value={cTitle} onChange={e => setCTitle(e.target.value)} placeholder="напр. Поддержка общественного транспорта" /></div>
            <div className="form-group"><label>Цель (что продвигать)</label>
              <textarea rows={2} value={cGoal} onChange={e => setCGoal(e.target.value)} placeholder="Продвигать развитие и финансирование общественного транспорта." /></div>
            <div className="form-group"><label>«Правда» / сторона / видение</label>
              <textarea rows={3} value={cStance} onChange={e => setCStance(e.target.value)} placeholder="Мировоззрение миссии: за что стоим, как смотрим на тему — этим агенты руководствуются в спорах." /></div>
            <div className="row-flex">
              <div className="form-group" style={{ flex: 1 }}><label>Тактика по умолчанию</label>
                <select value={cTactic} onChange={e => setCTactic(e.target.value)}>{TACTICS.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}</select></div>
              <div className="form-group"><label>Набор агентов</label>
                <select value={cMode} onChange={e => setCMode(e.target.value)}><option value="manual">вручную</option><option value="dynamic">динамически</option></select></div>
              {cMode === 'dynamic' && <div className="form-group"><label>Сколько</label>
                <input type="number" min={0} max={50} value={cCount} onChange={e => setCCount(parseInt(e.target.value) || 0)} style={{ width: 70 }} /></div>}
            </div>
            <div className="form-group"><label>Цели (каналы/посты, по одному в строке)</label>
              <textarea rows={3} value={cTargets} onChange={e => setCTargets(e.target.value)} placeholder={"@tashkent_news333\nhttps://t.me/somechannel/123"} /></div>
      </SidePanel>

      {selected && (
        <MissionDetail token={token} mission={selected} onClose={() => setSelected(null)}
          onChange={refreshSelected} setStatus={setStatus} removeMission={removeMission} />
      )}
    </div>
  );
};

const MissionDetail: React.FC<{
  token: string; mission: Mission; onClose: () => void; onChange: (m: Mission) => void;
  setStatus: (m: Mission, s: string) => void; removeMission: (m: Mission) => void;
}> = ({ token, mission, onClose, onChange, setStatus, removeMission }) => {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [tab, setTab] = useState<'overview' | 'targets' | 'agents'>('overview');
  const [m, setM] = useState<Mission>(mission);
  const [newTarget, setNewTarget] = useState('');
  const [eligible, setEligible] = useState<EligibleAgent[]>([]);

  const [title, setTitle] = useState(m.title);
  const [goal, setGoal] = useState(m.narrative_goal || '');
  const [stance, setStance] = useState(m.stance || '');
  const [tactic, setTactic] = useState(m.tactic);

  const apply = (u: Mission) => { setM(u); onChange(u); };

  const save = async () => {
    const res = await fetch(`/api/v1/missions/${m.id}`, { method: 'PUT', headers,
      body: JSON.stringify({ title, narrative_goal: goal, stance, tactic }) });
    if (res.ok) apply(await res.json());
  };
  const addTarget = async () => {
    const id = newTarget.trim(); if (!id) return;
    const res = await fetch(`/api/v1/missions/${m.id}/targets`, { method: 'POST', headers, body: JSON.stringify({ identifier: id, kind: inferKind(id) }) });
    if (res.ok) { apply(await res.json()); setNewTarget(''); }
  };
  const decideTarget = async (tid: number, decision: string) => {
    const res = await fetch(`/api/v1/missions/${m.id}/targets/${tid}/${decision}`, { method: 'POST', headers });
    if (res.ok) apply(await res.json());
  };
  const deleteTarget = async (tid: number) => {
    const res = await fetch(`/api/v1/missions/${m.id}/targets/${tid}`, { method: 'DELETE', headers });
    if (res.ok) apply(await res.json());
  };
  const removeAgent = async (sid: number) => {
    const res = await fetch(`/api/v1/missions/${m.id}/squad/${sid}`, { method: 'DELETE', headers });
    if (res.ok) apply(await res.json());
  };
  const fetchEligible = useCallback(async () => {
    const res = await fetch(`/api/v1/missions/${m.id}/eligible-agents`, { headers });
    if (res.ok) setEligible(await res.json());
  }, [m.id, token]);
  const enlist = async (agent_id: string, role: string) => {
    const res = await fetch(`/api/v1/missions/${m.id}/squad`, { method: 'POST', headers, body: JSON.stringify({ agent_id, assigned_role: role }) });
    if (res.ok) { apply(await res.json()); fetchEligible(); }
  };
  const autoAssign = async () => {
    const res = await fetch(`/api/v1/missions/${m.id}/auto-assign`, { method: 'POST', headers, body: JSON.stringify({ alpha: 1, beta: 2, gamma: 1 }) });
    if (res.ok) apply(await res.json());
  };

  useEffect(() => { if (tab === 'agents') fetchEligible(); }, [tab, fetchEligible]);

  return (
    <SidePanel
      open
      title={m.title}
      onClose={onClose}
      width={680}
      footer={<button className="btn-secondary" onClick={onClose}>Закрыть</button>}
    >
        <div className="md-detail-bar">
          {m.status === 'active'
            ? <button className="sc-btn-pause md-hdr-btn" onClick={() => setStatus(m, 'paused')}>⏸ Пауза</button>
            : <button className="sc-btn-resume md-hdr-btn" onClick={() => setStatus(m, 'active')}>▶ Возобновить</button>}
          <div className="tabs">
            <button className={`tab-btn ${tab === 'overview' ? 'active' : ''}`} onClick={() => setTab('overview')}>Обзор</button>
            <button className={`tab-btn ${tab === 'targets' ? 'active' : ''}`} onClick={() => setTab('targets')}>
              Цели ({m.summary.targets.active || 0}){(m.summary.targets.suggested || 0) > 0 ? ` · ⏳${m.summary.targets.suggested}` : ''}
            </button>
            <button className={`tab-btn ${tab === 'agents' ? 'active' : ''}`} onClick={() => setTab('agents')}>Агенты ({m.summary.agents.total})</button>
          </div>
        </div>
        <div className="md-detail-body">
          {tab === 'overview' && (
            <div className="form-grid">
              <div className="form-group full-width"><label>Название</label>
                <input value={title} onChange={e => setTitle(e.target.value)} /></div>
              <div className="form-group full-width"><label>Цель</label>
                <textarea rows={2} value={goal} onChange={e => setGoal(e.target.value)} /></div>
              <div className="form-group full-width"><label>«Правда» / сторона</label>
                <textarea rows={3} value={stance} onChange={e => setStance(e.target.value)} /></div>
              <div className="form-group"><label>Тактика по умолчанию</label>
                <select value={tactic} onChange={e => setTactic(e.target.value)}>{TACTICS.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}</select></div>
              <div className="form-group full-width">
                <button className="btn-primary" onClick={save}>Сохранить</button>
                <button className="btn-danger-text" style={{ marginLeft: 12 }} onClick={() => removeMission(m)}>Удалить миссию</button>
              </div>
            </div>
          )}

          {tab === 'targets' && (
            <>
              <div className="row-flex" style={{ alignItems: 'flex-end', marginBottom: 12 }}>
                <div className="form-group" style={{ flex: 1 }}><label>Добавить цель (@канал или ссылка на пост)</label>
                  <input value={newTarget} onChange={e => setNewTarget(e.target.value)} placeholder="@channel или https://t.me/.../123" /></div>
                <button className="btn-primary" onClick={addTarget}>Добавить</button>
              </div>
              {m.targets.length === 0 ? <p className="text-muted">Целей нет.</p> : (
                <div className="md-target-list">
                  {m.targets.map(t => (
                    <div key={t.id} className={`md-target st-${t.status}`}>
                      <div className="md-target-info">
                        <span className="md-target-id">{t.kind === 'post' ? '📄' : '📢'} {t.title || t.identifier}</span>
                        <span className="md-target-meta">
                          {t.identifier} · {t.source === 'agent' ? `предложил ${t.proposed_by || 'агент'}` : 'оператор'}
                          {t.status === 'rejected' ? ' · отклонено' : ''}
                        </span>
                        {t.reason && <span className="md-target-reason">{t.reason}</span>}
                      </div>
                      <div className="md-target-actions">
                        {t.status === 'suggested' && <>
                          <button className="sc-btn-resume" onClick={() => decideTarget(t.id, 'approve')}>✓ Принять</button>
                          <button className="sc-btn-pause" onClick={() => decideTarget(t.id, 'reject')}>✕ Отклонить</button>
                        </>}
                        <button className="btn-icon text-danger" onClick={() => deleteTarget(t.id)}>🗑</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {tab === 'agents' && (
            <>
              <div className="array-header"><h3>Ростер миссии</h3>
                <button className="btn-secondary" onClick={autoAssign}>⚡ Авто-набор (1α/2β/1γ)</button></div>
              {m.squad.length === 0 ? <p className="text-muted">Агенты не назначены.</p> : (
                <div className="md-roster">
                  {m.squad.map(s => (
                    <div key={s.id} className="md-roster-row">
                      <span className="sd-caste" style={{ color: ROLE_COLOR[s.assigned_role] }}>● {s.assigned_role}</span>
                      <span className="font-mono">{s.codename || s.agent_id}</span>
                      <button className="btn-icon text-danger" onClick={() => removeAgent(s.id)}>✕</button>
                    </div>
                  ))}
                </div>
              )}
              <div className="array-header mt-3"><h3>Доступные агенты</h3></div>
              <div className="md-eligible">
                {eligible.filter(e => !e.already_enlisted).map(e => (
                  <div key={e.agent_id} className="md-elig-row">
                    <span className="sd-caste" style={{ color: ROLE_COLOR[e.caste] }}>● {e.caste}</span>
                    <span className="font-mono">{e.codename || e.agent_id}</span>
                    <span className="text-muted" style={{ fontSize: '0.72rem' }}>совпадение {Math.round(e.match_score * 100)}% · загрузка {e.active_mission_load}</span>
                    {e.at_capacity ? <span className="text-muted">на пределе</span> :
                      <button className="btn-secondary" onClick={() => enlist(e.agent_id, e.caste)}>+ в миссию</button>}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
    </SidePanel>
  );
};

export default MissionDeck;
