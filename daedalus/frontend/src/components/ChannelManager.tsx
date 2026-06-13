import { useState, useEffect, useCallback } from 'react';
import './ChannelManager.css';

interface Channel {
  chat_id: string;
  title: string;
  username: string | null;
  type: string;
  members: number | null;
  role: 'target' | 'news' | 'ignored';
  watching: boolean;
  synced_at?: string | null;
}

interface ActionLog {
  id: number;
  action_type: string;
  target_url: string;
  text_content: string | null;
  status: string;
  created_at: string;
}

interface ChannelManagerProps {
  token: string;
  agentId: string;
  label: string;
  onClose: () => void;
}

const ROLE_META: Record<string, { label: string; cls: string }> = {
  target: { label: 'Цель', cls: 'role-target' },
  news: { label: 'Новости', cls: 'role-news' },
  ignored: { label: 'Игнор', cls: 'role-ignored' },
};
const ROLES: Array<'target' | 'news' | 'ignored'> = ['target', 'news', 'ignored'];

const ChannelManager: React.FC<ChannelManagerProps> = ({ token, agentId, label, onClose }) => {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [syncedAt, setSyncedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [actions, setActions] = useState<ActionLog[]>([]);
  const [tab, setTab] = useState<'channels' | 'actions'>('channels');

  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<string>('all');
  const [watchFilter, setWatchFilter] = useState<string>('all');

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const apply = (data: any) => {
    setChannels(data.channels || []);
    setCounts(data.counts || {});
    setSyncedAt(data.synced_at || null);
    if (data.error) setError(`Сессия недоступна: ${data.error}`); else setError('');
  };

  const fetchChannels = useCallback(async () => {
    setLoading(true);
    try {
      // Cached read — instant after the first enumeration.
      const res = await fetch(`/api/v1/souls/agents/${agentId}/channels`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      apply(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки');
    }
    setLoading(false);
  }, [agentId, token]);

  const refresh = async () => {
    setRefreshing(true);
    setError('');
    try {
      const res = await fetch(`/api/v1/souls/agents/${agentId}/channels/sync`, { method: 'POST', headers });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || `HTTP ${res.status}`);
      }
      apply(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось обновить');
    }
    setRefreshing(false);
  };

  const fetchActions = useCallback(async () => {
    try {
      const res = await fetch(`/api/v1/analytics/stream?agent_id=${encodeURIComponent(agentId)}&limit=25`, { headers });
      if (res.ok) setActions((await res.json()).logs || []);
    } catch { /* best-effort */ }
  }, [agentId, token]);

  useEffect(() => { fetchChannels(); fetchActions(); }, [fetchChannels, fetchActions]);

  const update = async (ch: Channel, patch: Partial<Pick<Channel, 'role' | 'watching'>>) => {
    setChannels(prev => prev.map(c => c.chat_id === ch.chat_id ? { ...c, ...patch } : c));
    try {
      const res = await fetch(`/api/v1/souls/agents/${agentId}/channels/${encodeURIComponent(ch.chat_id)}`, {
        method: 'POST', headers, body: JSON.stringify(patch),
      });
      if (res.ok) recount();
    } catch { fetchChannels(); }
  };

  const recount = () => {
    setChannels(prev => {
      setCounts(ROLES.reduce((acc, r) => ({ ...acc, [r]: prev.filter(c => c.role === r).length }), {}));
      return prev;
    });
  };

  const filtered = channels.filter(c => {
    if (roleFilter !== 'all' && c.role !== roleFilter) return false;
    if (watchFilter === 'on' && !c.watching) return false;
    if (watchFilter === 'off' && c.watching) return false;
    if (query.trim()) {
      const q = query.toLowerCase();
      if (!`${c.title || ''} ${c.username || ''} ${c.chat_id}`.toLowerCase().includes(q)) return false;
    }
    return true;
  });

  // Bulk action over the currently filtered set.
  const bulk = async (patch: { role?: string; watching?: boolean }) => {
    const chat_ids = filtered.map(c => c.chat_id);
    if (chat_ids.length === 0) return;
    setChannels(prev => prev.map(c => chat_ids.includes(c.chat_id) ? { ...c, ...patch } as Channel : c));
    try {
      const res = await fetch(`/api/v1/souls/agents/${agentId}/channels/bulk`, {
        method: 'POST', headers, body: JSON.stringify({ chat_ids, ...patch }),
      });
      if (res.ok) apply(await res.json());
    } catch { fetchChannels(); }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content large cm-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Каналы аккаунта · {label}</h2>
          <div className="tabs">
            <button className={`tab-btn ${tab === 'channels' ? 'active' : ''}`} onClick={() => setTab('channels')}>
              Каналы {channels.length ? `(${channels.length})` : ''}
            </button>
            <button className={`tab-btn ${tab === 'actions' ? 'active' : ''}`} onClick={() => setTab('actions')}>
              Действия бота
            </button>
            <button className="btn-secondary cm-refresh" onClick={refresh} disabled={refreshing}>
              {refreshing ? '⏳ Опрос…' : '↻ Обновить из Telegram'}
            </button>
          </div>
        </div>

        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          {tab === 'channels' && (
            <>
              <div className="cm-topbar">
                <span className="cm-counts">
                  <span className="role-target">Цели: {counts.target ?? 0}</span>
                  <span className="role-news">Новости: {counts.news ?? 0}</span>
                  <span className="role-ignored">Игнор: {counts.ignored ?? 0}</span>
                </span>
                <span className="cm-synced">{syncedAt ? `обновлено: ${new Date(syncedAt).toLocaleString('ru-RU')}` : 'из кэша'}</span>
              </div>

              {/* Search + filters */}
              <div className="cm-filterbar">
                <input className="cm-search" placeholder="🔍 Поиск канала…" value={query} onChange={e => setQuery(e.target.value)} />
                <div className="cm-filter-pills">
                  <button className={`pill ${roleFilter === 'all' ? 'selected' : ''}`} onClick={() => setRoleFilter('all')}>все роли</button>
                  {ROLES.map(r => (
                    <button key={r} className={`pill ${roleFilter === r ? 'selected' : ''}`} onClick={() => setRoleFilter(r)}>{ROLE_META[r].label}</button>
                  ))}
                  <button className={`pill ${watchFilter === 'on' ? 'selected' : ''}`} onClick={() => setWatchFilter(watchFilter === 'on' ? 'all' : 'on')}>👁 слежу</button>
                  <button className={`pill ${watchFilter === 'off' ? 'selected' : ''}`} onClick={() => setWatchFilter(watchFilter === 'off' ? 'all' : 'off')}>⏸ пауза</button>
                </div>
              </div>

              {/* Bulk actions over visible */}
              <div className="cm-bulkbar">
                <span>Ко всем видимым ({filtered.length}):</span>
                {ROLES.map(r => (
                  <button key={r} className={`cm-role-btn ${ROLE_META[r].cls}`} onClick={() => bulk({ role: r })}>→ {ROLE_META[r].label}</button>
                ))}
                <button className="cm-watch on" onClick={() => bulk({ watching: true })}>👁 слежу</button>
                <button className="cm-watch off" onClick={() => bulk({ watching: false })}>⏸ пауза</button>
              </div>

              {loading ? (
                <p className="cm-loading">⏳ Загрузка…</p>
              ) : filtered.length === 0 ? (
                <p className="text-muted">{channels.length === 0 ? 'Кэш пуст — нажмите «↻ Обновить из Telegram».' : 'Ничего не найдено по фильтру.'}</p>
              ) : (
                <div className="cm-list">
                  {filtered.map(ch => (
                    <div key={ch.chat_id} className={`cm-row ${!ch.watching ? 'paused' : ''}`}>
                      <div className="cm-ch-info">
                        <span className="cm-ch-title">{ch.title || ch.username || ch.chat_id}</span>
                        <span className="cm-ch-meta">
                          {ch.username ? `@${ch.username}` : ch.chat_id} · {ch.type}
                          {ch.members ? ` · ${ch.members.toLocaleString('ru-RU')} уч.` : ''}
                        </span>
                      </div>
                      <div className="cm-roles">
                        {ROLES.map(r => (
                          <button key={r} className={`cm-role-btn ${ROLE_META[r].cls} ${ch.role === r ? 'active' : ''}`} onClick={() => update(ch, { role: r })}>
                            {ROLE_META[r].label}
                          </button>
                        ))}
                      </div>
                      <button className={`cm-watch ${ch.watching ? 'on' : 'off'}`} onClick={() => update(ch, { watching: !ch.watching })}
                        title={ch.watching ? 'слежу — нажмите чтобы остановить' : 'на паузе — нажмите чтобы продолжить'}>
                        {ch.watching ? '👁 слежу' : '⏸ пауза'}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {tab === 'actions' && (
            <>
              <p className="help-text">Что система (бот) делала на этом аккаунте — последние действия.</p>
              {actions.length === 0 ? (
                <p className="text-muted">Действий пока нет.</p>
              ) : (
                <div className="cm-actions">
                  {actions.map(a => (
                    <div key={a.id} className={`cm-action st-${a.status.toLowerCase()}`}>
                      <span className="cm-action-time">{new Date(a.created_at).toLocaleString('ru-RU')}</span>
                      <span className="cm-action-type">{a.action_type}</span>
                      <span className={`status-badge ${a.status.toLowerCase()}`}>{a.status}</span>
                      <a className="cm-action-url" href={a.target_url} target="_blank" rel="noreferrer">{a.target_url}</a>
                      {a.text_content && <div className="cm-action-text">«{a.text_content}»</div>}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
};

export default ChannelManager;
