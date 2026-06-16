import React, { useEffect, useState, useCallback } from 'react';
import { ListChecks, RefreshCw, Check, X, Ban } from 'lucide-react';
import './DecisionLog.css';

interface Decision {
  id: number;
  agent_id: string | null;
  mission_id: number | null;
  channel_ref: string | null;
  post_url: string | null;
  kind: string;
  detail: string | null;
  verdict: boolean | null;
  created_at: string | null;
}

interface Props { token: string; }

const DecisionLog: React.FC<Props> = ({ token }) => {
  const [rows, setRows] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [kind, setKind] = useState('');

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const fetchRows = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '150' });
      if (kind) params.set('kind', kind);
      const res = await fetch(`/api/v1/decisions?${params}`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRows(Array.isArray(data.decisions) ? data.decisions : []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить решения');
    } finally {
      setLoading(false);
    }
  }, [kind, token]);

  useEffect(() => { fetchRows(); }, [fetchRows]);

  const verdictBadge = (d: Decision) => {
    if (d.kind === 'skip') return <span className="dl-badge dl-skip"><Ban size={12} /> пропуск</span>;
    if (d.verdict === true) return <span className="dl-badge dl-yes"><Check size={12} /> по теме</span>;
    if (d.verdict === false) return <span className="dl-badge dl-no"><X size={12} /> не по теме</span>;
    return <span className="dl-badge">—</span>;
  };

  const channelLabel = (ref: string | null) => (ref || '—');

  return (
    <div className="decision-log view-container">
      <div className="header-row">
        <div>
          <h1><ListChecks size={22} style={{ verticalAlign: '-4px' }} /> Решения</h1>
          <p className="subtitle">Почему рой отреагировал или нет: что бот распознал в посте (текст / расшифровка аудио / OCR фото), вердикт релевантности и причина пропуска. История (Live Ops показывает то же в реальном времени).</p>
        </div>
        <div className="header-actions">
          <select className="dl-select" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">Все типы</option>
            <option value="relevance">Релевантность</option>
            <option value="skip">Пропуски</option>
          </select>
          <button className="btn-secondary" onClick={fetchRows}><RefreshCw size={14} /> Обновить</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="data-grid-container">
        {loading && rows.length === 0 ? <p>Загрузка…</p> : (
          <table className="data-grid">
            <thead>
              <tr>
                <th>Время</th>
                <th>Агент</th>
                <th>Канал</th>
                <th>Вердикт</th>
                <th>Что распознал / причина</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={6} className="empty-state">Решений пока нет. Они пишутся, когда движок проверяет посты целевых каналов активных миссий.</td></tr>
              ) : rows.map(d => (
                <tr key={d.id}>
                  <td className="dl-time">{d.created_at ? new Date(d.created_at).toLocaleString('ru-RU') : '—'}</td>
                  <td className="dl-agent">{d.agent_id || '—'}</td>
                  <td className="dl-channel">{channelLabel(d.channel_ref)}</td>
                  <td>{verdictBadge(d)}</td>
                  <td className="dl-detail">{d.detail || '—'}</td>
                  <td>{d.post_url ? <a className="dl-link" href={d.post_url} target="_blank" rel="noreferrer">пост ↗</a> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default DecisionLog;
