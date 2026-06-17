import { useEffect, useState, useCallback } from 'react';
import { DataTable, Column } from './DataTable';
import './SwarmDashboard.css';

interface SwarmData {
  today: Record<string, number>;
  all_time: Record<string, number>;
  success_24h: number;
  failed_24h: number;
  by_caste: Record<string, Record<string, number>>;
  by_agent: Array<{ agent_id: string; caste: string; status: string; comment: number; reply: number; react: number; last_active: string | null }>;
  agents_by_status: Record<string, number>;
  knowledge: { total: number; today: number };
  live: { active_dialogues: number; target_channels: number; news_channels: number };
}

interface ActivityLog {
  id: number; agent_id: string; action_type: string; target_url: string; text_content: string | null; status: string; created_at: string;
}
interface Dialogue {
  agent_id: string; channel: string; post_id: number; url: string; opponent_id: string | null; depth: number; narrative_goal: string;
}

interface Props {
  token: string;
  onNavigate?: (view: string) => void;
}

const CASTE_COLOR: Record<string, string> = { alpha: '#ef4444', beta: '#3b82f6', gamma: '#22c55e' };
const ACTION_LABEL: Record<string, string> = { comment: 'Комментарии', reply: 'Ответы людям', react: 'Реакции' };

const SwarmDashboard: React.FC<Props> = ({ token, onNavigate }) => {
  const [d, setD] = useState<SwarmData | null>(null);
  const [error, setError] = useState('');

  // Drill-down modal state.
  const [drill, setDrill] = useState<{ title: string; kind: 'activity' | 'dialogues' } | null>(null);
  const [logs, setLogs] = useState<ActivityLog[]>([]);
  const [dialogues, setDialogues] = useState<Dialogue[]>([]);
  const [drillLoading, setDrillLoading] = useState(false);

  const headers = { Authorization: `Bearer ${token}` };

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/analytics/swarm', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setD(await res.json());
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить');
    }
  }, [token]);

  useEffect(() => {
    fetchData();
    const iv = setInterval(() => { if (!drill) fetchData(); }, 10000);
    return () => clearInterval(iv);
  }, [fetchData, drill]);

  // Open an activity drill-down (optionally filtered by action_type / agent set).
  const openActivity = async (title: string, opts: { action_type?: string; agent_id?: string; agentIds?: string[] }) => {
    setDrill({ title, kind: 'activity' });
    setDrillLoading(true);
    try {
      const p = new URLSearchParams({ since_hours: '24', limit: '150' });
      if (opts.action_type) p.set('action_type', opts.action_type);
      if (opts.agent_id) p.set('agent_id', opts.agent_id);
      const res = await fetch(`/api/v1/analytics/stream?${p}`, { headers });
      let rows: ActivityLog[] = (await res.json()).logs || [];
      if (opts.agentIds) rows = rows.filter(r => opts.agentIds!.includes(r.agent_id));
      setLogs(rows);
    } catch { setLogs([]); }
    setDrillLoading(false);
  };

  const openDialogues = async () => {
    setDrill({ title: 'Активные диалоги', kind: 'dialogues' });
    setDrillLoading(true);
    try {
      const res = await fetch('/api/v1/analytics/dialogues', { headers });
      setDialogues((await res.json()).dialogues || []);
    } catch { setDialogues([]); }
    setDrillLoading(false);
  };

  const t = d?.today || {};
  const all = d?.all_time || {};
  const agentsByCaste = (caste: string) => (d?.by_agent || []).filter(a => a.caste === caste).map(a => a.agent_id);

  const activityColumns: Column<ActivityLog>[] = [
    { key: 'created_at', header: 'Время', width: '160px', sortValue: l => l.created_at,
      render: l => <span className="sd-drill-time">{new Date(l.created_at).toLocaleString('ru-RU')}</span> },
    { key: 'agent_id', header: 'Агент', width: '170px', sortValue: l => l.agent_id,
      render: l => <span className="font-mono sd-muted">{l.agent_id}</span> },
    { key: 'action_type', header: 'Действие', width: '130px', sortValue: l => l.action_type,
      render: l => <span className="sd-drill-act">{ACTION_LABEL[l.action_type] || l.action_type}</span> },
    { key: 'status', header: 'Статус', width: '100px', sortValue: l => l.status,
      render: l => <span className={`sd-status ${l.status === 'SUCCESS' ? 'active' : 'suspended'}`}>{l.status}</span> },
    { key: 'text_content', header: 'Текст / ссылка', sortable: false,
      render: l => (
        <>
          {l.text_content && <div className="sd-drill-text">«{l.text_content}»</div>}
          {l.target_url && <a className="sd-drill-url" href={l.target_url} target="_blank" rel="noreferrer">{l.target_url}</a>}
        </>
      ) },
  ];

  const dialogueColumns: Column<Dialogue>[] = [
    { key: 'agent_id', header: 'Агент', width: '170px', sortValue: dl => dl.agent_id,
      render: dl => <span className="font-mono sd-muted">{dl.agent_id}</span> },
    { key: 'channel', header: 'Канал', sortValue: dl => dl.channel,
      render: dl => <span>📢 {dl.channel}</span> },
    { key: 'depth', header: 'Глубина', width: '90px', align: 'right', sortValue: dl => dl.depth },
    { key: 'opponent_id', header: 'Оппонент', sortValue: dl => dl.opponent_id || '',
      render: dl => <span className="sd-muted">{dl.opponent_id || '—'}</span> },
    { key: 'narrative_goal', header: 'Цель / ссылка', sortable: false,
      render: dl => (
        <>
          {dl.narrative_goal && <div className="sd-drill-text">{dl.narrative_goal}</div>}
          {dl.url && <a className="sd-drill-url" href={dl.url} target="_blank" rel="noreferrer">{dl.url}</a>}
        </>
      ) },
  ];

  const card = (icon: string, label: string, today: number, total: number, onClick?: () => void) => (
    <div className={`sd-card ${onClick ? 'clickable' : ''}`} onClick={onClick}>
      <div className="sd-card-icon">{icon}</div>
      <div className="sd-card-num">{today}</div>
      <div className="sd-card-label">{label} <span className="sd-muted">сегодня</span></div>
      <div className="sd-card-total">всего: {total}{onClick ? '  ›' : ''}</div>
    </div>
  );

  return (
    <div className="swarm-dashboard view-container">
      <div className="header-row">
        <div>
          <h1>Дашборд роя</h1>
          <p className="subtitle">Кликните по любой цифре, чтобы увидеть конкретные записи.</p>
        </div>
        <span className="sd-live-tag"><span className="sd-dot" /> авто-обновление</span>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {!d ? <p>Загрузка…</p> : (
        <>
          <div className="sd-cards">
            {card('✉️', 'Комментарии', t.comment || 0, all.comment || 0, () => openActivity('Комментарии (24ч)', { action_type: 'comment' }))}
            {card('💬', 'Ответы людям', t.reply || 0, all.reply || 0, () => openActivity('Ответы людям (24ч)', { action_type: 'reply' }))}
            {card('👍', 'Реакции', t.react || 0, all.react || 0, () => openActivity('Реакции (24ч)', { action_type: 'react' }))}
            {card('🗞', 'Новости в знания', d.knowledge.today, d.knowledge.total, onNavigate ? () => onNavigate('muninn') : undefined)}
          </div>

          <div className="sd-row2">
            <div className="sd-panel">
              <h3>По кастам (24ч) <span className="sd-muted">— клик по строке</span></h3>
              <table className="sd-table">
                <thead><tr><th>Каста</th><th>✉️ комм.</th><th>💬 ответы</th><th>👍 реакции</th></tr></thead>
                <tbody>
                  {(['alpha', 'beta', 'gamma'] as const).map(c => (
                    <tr key={c} className="sd-clickable" onClick={() => openActivity(`Каста ${c} (24ч)`, { agentIds: agentsByCaste(c) })}>
                      <td><span className="sd-caste" style={{ color: CASTE_COLOR[c] }}>● {c}</span></td>
                      <td>{d.by_caste[c]?.comment ?? 0}</td>
                      <td>{d.by_caste[c]?.reply ?? 0}</td>
                      <td>{d.by_caste[c]?.react ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="sd-panel">
              <h3>Сейчас</h3>
              <div className="sd-stat sd-clickable" onClick={openDialogues}><span>🗣 Активные диалоги ›</span><b>{d.live.active_dialogues}</b></div>
              <div className={`sd-stat ${onNavigate ? 'sd-clickable' : ''}`} onClick={onNavigate ? () => onNavigate('souls') : undefined}><span>🎯 Целевые каналы {onNavigate ? '›' : ''}</span><b>{d.live.target_channels}</b></div>
              <div className={`sd-stat ${onNavigate ? 'sd-clickable' : ''}`} onClick={onNavigate ? () => onNavigate('souls') : undefined}><span>📰 Новостные каналы {onNavigate ? '›' : ''}</span><b>{d.live.news_channels}</b></div>
              <div className="sd-stat"><span>🤖 Агенты активны</span><b>{d.agents_by_status['active'] ?? 0}</b></div>
              <div className="sd-stat"><span>⏸ На паузе</span><b>{d.agents_by_status['suspended'] ?? 0}</b></div>
              <div className="sd-stat"><span>✅ Успех / ❌ сбой (24ч)</span><b>{d.success_24h} / {d.failed_24h}</b></div>
            </div>
          </div>

          <div className="sd-panel">
            <h3>По агентам (24ч) <span className="sd-muted">— клик по строке</span></h3>
            <table className="sd-table">
              <thead><tr><th>Агент</th><th>Каста</th><th>Статус</th><th>✉️</th><th>💬</th><th>👍</th><th>Последняя активность</th></tr></thead>
              <tbody>
                {d.by_agent.length === 0 ? (
                  <tr><td colSpan={7} className="sd-muted" style={{ textAlign: 'center', padding: 16 }}>За сутки активности нет.</td></tr>
                ) : d.by_agent.map(a => (
                  <tr key={a.agent_id} className="sd-clickable" onClick={() => openActivity(`Агент ${a.agent_id} (24ч)`, { agent_id: a.agent_id })}>
                    <td className="font-mono">{a.agent_id}</td>
                    <td><span className="sd-caste" style={{ color: CASTE_COLOR[a.caste] || '#94a3b8' }}>● {a.caste}</span></td>
                    <td><span className={`sd-status ${a.status}`}>{a.status}</span></td>
                    <td>{a.comment}</td><td>{a.reply}</td><td>{a.react}</td>
                    <td className="sd-muted">{a.last_active ? new Date(a.last_active).toLocaleString('ru-RU') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {drill && (
        <div className="modal-overlay" onClick={() => setDrill(null)}>
          <div className="modal-content large" onClick={e => e.stopPropagation()}>
            <div className="modal-header"><h2>{drill.title}</h2></div>
            <div className="modal-body">
              {drillLoading ? <p>Загрузка…</p> : drill.kind === 'activity' ? (
                <DataTable
                  columns={activityColumns}
                  rows={logs}
                  rowKey={l => l.id}
                  searchText={l => `${l.agent_id} ${ACTION_LABEL[l.action_type] || l.action_type} ${l.text_content || ''} ${l.target_url || ''}`}
                  searchPlaceholder="🔍 Поиск по агенту, тексту или каналу…"
                  emptyText="Записей нет."
                  pageSize={25}
                />
              ) : (
                <DataTable
                  columns={dialogueColumns}
                  rows={dialogues}
                  rowKey={dl => `${dl.agent_id}:${dl.channel}:${dl.post_id}:${dl.depth}`}
                  searchText={dl => `${dl.agent_id} ${dl.channel} ${dl.opponent_id || ''} ${dl.narrative_goal || ''}`}
                  searchPlaceholder="🔍 Поиск по агенту, каналу или цели…"
                  emptyText="Активных диалогов нет."
                  pageSize={25}
                />
              )}
            </div>
            <div className="modal-actions"><button className="btn-secondary" onClick={() => setDrill(null)}>Закрыть</button></div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SwarmDashboard;
