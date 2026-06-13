import React, { useState, useEffect, useRef, useCallback } from 'react';
import './LiveOps.css';

interface AgentEvent {
  id: string;
  agent_id: string;
  service: string;
  event: string;
  detail: string;
  status: string;
  target: string;
  ts: string;
}

interface AgentStatus {
  agent_id: string;
  event: string;
  detail: string;
  status: string;
  target: string;
  service: string;
  ts: string;
  id: string;
}

interface LiveOpsProps {
  token: string;
}

// Machine event key → emoji icon + human label. Anything unknown falls back.
const EVENT_META: Record<string, { icon: string; label: string }> = {
  poll:             { icon: '🔄', label: 'Поллинг диалогов' },
  target_scan:      { icon: '🎯', label: 'Сканирует целевые каналы' },
  target_post:      { icon: '🎯', label: 'Релевантный пост в цели' },
  reply_detected:   { icon: '👋', label: 'Получен ответ человека' },
  reading_post:     { icon: '👁', label: 'Читает пост' },
  reading_thread:   { icon: '🌡', label: 'Оценивает настроение треда' },
  reading_news:     { icon: '📰', label: 'Читает новость' },
  thinking:         { icon: '💭', label: 'Сочиняет' },
  memory_read:      { icon: '🧠', label: 'Вспоминает' },
  memory_write:     { icon: '🧠', label: 'Обновляет память' },
  generated:        { icon: '✨', label: 'Готов текст' },
  guardrail_reject: { icon: '♻️', label: 'Переписывает черновик' },
  posting:          { icon: '✍️', label: 'Публикует' },
  commented:        { icon: '✉️', label: 'Опубликовал комментарий' },
  replied:          { icon: '💬', label: 'Ответил человеку' },
  dispatched:       { icon: '📤', label: 'Отправил в исполнение' },
  error:            { icon: '⚠️', label: 'Ошибка' },
};

const AGENT_COLORS = ['#5b8def', '#34c98b', '#e0a23c', '#c850c0', '#48c6ef', '#f76b8a', '#9b87f5', '#ff9f43'];

function agentColor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return AGENT_COLORS[h % AGENT_COLORS.length];
}

function eventMeta(ev: string) {
  return EVENT_META[ev] || { icon: '•', label: ev };
}

function fmtTime(ts: string): string {
  const t = parseFloat(ts);
  if (!t) return '';
  return new Date(t * 1000).toLocaleTimeString('ru-RU', { hour12: false });
}

// "working" while a fresh active step is in flight; "idle" if seen recently; else "asleep".
function liveness(a: AgentStatus): 'working' | 'idle' | 'asleep' {
  const ageMs = Date.now() - parseFloat(a.ts) * 1000;
  if (a.status === 'active' && ageMs < 90_000) return 'working';
  if (ageMs < 300_000) return 'idle';
  return 'asleep';
}

const POLL_MS = 1200;
const MAX_EVENTS = 400;

const LiveOps: React.FC<LiveOpsProps> = ({ token }) => {
  const [events, setEvents] = useState<AgentEvent[]>([]); // newest first
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const lastId = useRef<string>('0');
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const poll = useCallback(async () => {
    if (pausedRef.current) return;
    try {
      const after = lastId.current && lastId.current !== '0' ? `&after=${encodeURIComponent(lastId.current)}` : '';
      const res = await fetch(`/api/v1/analytics/live?limit=300${after}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConnected(true);
      setError('');
      if (data.last_id && data.last_id !== '0') lastId.current = data.last_id;
      setAgents(data.agents || []);
      const fresh: AgentEvent[] = data.events || [];
      if (fresh.length) {
        // backlog comes oldest→newest; reverse so newest sits on top, then prepend.
        const reversed = [...fresh].reverse();
        setEvents(prev => [...reversed, ...prev].slice(0, MAX_EVENTS));
      }
    } catch (e: unknown) {
      setConnected(false);
      setError(e instanceof Error ? e.message : 'stream error');
    }
  }, [token]);

  useEffect(() => {
    poll();
    const iv = setInterval(poll, POLL_MS);
    return () => clearInterval(iv);
  }, [poll]);

  const filtered = selected ? events.filter(e => e.agent_id === selected) : events;
  // Collapse consecutive identical events (e.g. the idle poll heartbeat) into one
  // row with a ×N counter, so quiet periods don't flood the feed.
  type Group = AgentEvent & { count: number };
  const shown: Group[] = [];
  for (const e of filtered) {
    const last = shown[shown.length - 1];
    if (last && last.agent_id === e.agent_id && last.event === e.event && last.detail === e.detail) {
      last.count += 1;
    } else {
      shown.push({ ...e, count: 1 });
    }
  }
  const sortedAgents = [...agents].sort((a, b) => {
    const order = { working: 0, idle: 1, asleep: 2 } as const;
    return order[liveness(a)] - order[liveness(b)] || a.agent_id.localeCompare(b.agent_id);
  });

  return (
    <div className="liveops view-container">
      <div className="lo-header">
        <div className="lo-title">
          <h1>Командный центр</h1>
          <span className={`lo-live ${connected && !paused ? 'on' : 'off'}`}>
            <span className="lo-live-dot" /> {paused ? 'ПАУЗА' : connected ? 'LIVE' : 'НЕТ СВЯЗИ'}
          </span>
        </div>
        <div className="lo-actions">
          {selected && (
            <button className="lo-chip-clear" onClick={() => setSelected(null)}>
              фильтр: {selected} ✕
            </button>
          )}
          <button className="lo-pause" onClick={() => setPaused(p => !p)}>
            {paused ? '▶ Возобновить' : '⏸ Пауза'}
          </button>
        </div>
      </div>

      {error && <div className="lo-error">Поток недоступен: {error}</div>}

      <div className="lo-body">
        {/* Agent rail */}
        <aside className="lo-rail">
          <div className="lo-rail-head">АГЕНТЫ ({agents.length})</div>
          {sortedAgents.length === 0 && <div className="lo-rail-empty">Тихо. Никто пока не активен.</div>}
          {sortedAgents.map(a => {
            const live = liveness(a);
            const meta = eventMeta(a.event);
            return (
              <button
                key={a.agent_id}
                className={`lo-agent ${live} ${selected === a.agent_id ? 'sel' : ''}`}
                onClick={() => setSelected(selected === a.agent_id ? null : a.agent_id)}
              >
                <span className="lo-agent-dot" style={{ background: agentColor(a.agent_id) }} />
                <span className="lo-agent-body">
                  <span className="lo-agent-name">{a.agent_id}</span>
                  <span className="lo-agent-now">
                    {meta.icon} {a.detail || meta.label}
                  </span>
                  {live === 'working' && <span className="lo-shimmer" />}
                </span>
                <span className={`lo-agent-state ${live}`}>
                  {live === 'working' ? 'работает' : live === 'idle' ? 'ожидает' : 'спит'}
                </span>
              </button>
            );
          })}
        </aside>

        {/* Chronological feed */}
        <main className="lo-feed">
          {shown.length === 0 ? (
            <div className="lo-feed-empty">
              <p>Лента пуста.</p>
              <p className="muted">Как только бот начнёт работать (поллинг, чтение, ответ) — события появятся здесь в реальном времени.</p>
            </div>
          ) : (
            shown.map(e => {
              const meta = eventMeta(e.event);
              return (
                <div key={e.id} className={`lo-row st-${e.status}`}>
                  <span className="lo-time">{fmtTime(e.ts)}</span>
                  <span className="lo-icon">{meta.icon}</span>
                  <span className="lo-agent-chip" style={{ color: agentColor(e.agent_id), borderColor: agentColor(e.agent_id) }}>
                    {e.agent_id}
                  </span>
                  <span className="lo-detail">{e.detail || meta.label}</span>
                  {e.count > 1 && <span className="lo-count">×{e.count}</span>}
                  {e.target && <span className="lo-target" title={e.target}>{e.target}</span>}
                </div>
              );
            })
          )}
        </main>
      </div>
    </div>
  );
};

export default LiveOps;
