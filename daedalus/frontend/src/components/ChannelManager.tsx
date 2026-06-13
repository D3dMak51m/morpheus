import { useState, useEffect, useCallback } from 'react';
import './ChannelManager.css';

interface Channel {
  chat_id: string;
  title: string;
  username: string | null;
  type: string;
  members: number | null;
  unread: number | null;
  role: 'target' | 'news' | 'ignored';
  watching: boolean;
  stale?: boolean;
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actions, setActions] = useState<ActionLog[]>([]);
  const [tab, setTab] = useState<'channels' | 'actions'>('channels');

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const fetchChannels = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`/api/v1/souls/agents/${agentId}/channels`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setChannels(data.channels || []);
      setCounts(data.counts || {});
      if (data.error) setError(`Не удалось опросTG-сессию: ${data.error}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки каналов');
    }
    setLoading(false);
  }, [agentId, token]);

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
      await fetch(`/api/v1/souls/agents/${agentId}/channels/${encodeURIComponent(ch.chat_id)}`, {
        method: 'POST', headers, body: JSON.stringify(patch),
      });
      // refresh counts cheaply
      setCounts(prev => {
        const next = { ...prev };
        if (patch.role) {
          next[patch.role] = (next[patch.role] || 0) + 1;
          if (next[ch.role] > 0) next[ch.role] -= 1;
        }
        return next;
      });
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
            <button className="btn-secondary cm-refresh" onClick={fetchChannels}>↻ Обновить</button>
          </div>
        </div>

        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          {tab === 'channels' && (
            <>
              <p className="help-text">
                Это все каналы и группы, на которые подписан аккаунт — потенциальные цели и источники новостей агента.
                Отметьте роль и включите/выключите слежение.
              </p>
              <div className="cm-counts">
                <span className="role-target">Цели: {counts.target ?? 0}</span>
                <span className="role-news">Новости: {counts.news ?? 0}</span>
                <span className="role-ignored">Игнор: {counts.ignored ?? 0}</span>
              </div>

              {loading ? (
                <p className="cm-loading">⏳ Опрашиваю Telegram-сессию аккаунта… (несколько секунд)</p>
              ) : channels.length === 0 ? (
                <p className="text-muted">Каналы не найдены (аккаунт ни на что не подписан или сессия недоступна).</p>
              ) : (
                <div className="cm-list">
                  {channels.map(ch => (
                    <div key={ch.chat_id} className={`cm-row ${!ch.watching ? 'paused' : ''}`}>
                      <div className="cm-ch-info">
                        <span className="cm-ch-title">
                          {ch.title || ch.username || ch.chat_id}
                          {ch.stale && <span className="cm-stale" title="аккаунт больше не в этом канале">архив</span>}
                        </span>
                        <span className="cm-ch-meta">
                          {ch.username ? `@${ch.username}` : ch.chat_id} · {ch.type}
                          {ch.members ? ` · ${ch.members.toLocaleString('ru-RU')} уч.` : ''}
                        </span>
                      </div>
                      <div className="cm-roles">
                        {ROLES.map(r => (
                          <button
                            key={r}
                            className={`cm-role-btn ${ROLE_META[r].cls} ${ch.role === r ? 'active' : ''}`}
                            onClick={() => update(ch, { role: r })}
                          >
                            {ROLE_META[r].label}
                          </button>
                        ))}
                      </div>
                      <button
                        className={`cm-watch ${ch.watching ? 'on' : 'off'}`}
                        onClick={() => update(ch, { watching: !ch.watching })}
                        title={ch.watching ? 'слежу — нажмите чтобы остановить' : 'на паузе — нажмите чтобы продолжить'}
                      >
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
