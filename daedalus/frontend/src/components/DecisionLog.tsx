import React, { useEffect, useState, useCallback } from 'react';
import { ListChecks, RefreshCw, Check, X, Ban } from 'lucide-react';
import { DataTable, Column } from './DataTable';
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
      const params = new URLSearchParams({ limit: '300' });
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

  const columns: Column<Decision>[] = [
    { key: 'created_at', header: 'Время', width: '150px',
      sortValue: d => d.created_at || '',
      render: d => <span className="dl-time">{d.created_at ? new Date(d.created_at).toLocaleString('ru-RU') : '—'}</span> },
    { key: 'agent_id', header: 'Агент', render: d => <span className="dl-agent">{d.agent_id || '—'}</span> },
    { key: 'channel_ref', header: 'Канал', render: d => <span className="dl-channel">{d.channel_ref || '—'}</span> },
    { key: 'verdict', header: 'Вердикт', width: '120px', sortValue: d => d.kind === 'skip' ? 2 : d.verdict ? 1 : 0,
      render: verdictBadge },
    { key: 'detail', header: 'Что распознал / причина', sortable: false,
      render: d => <span className="dl-detail">{d.detail || '—'}</span> },
    { key: 'post', header: '', sortable: false, width: '70px',
      render: d => d.post_url ? <a className="dl-link" href={d.post_url} target="_blank" rel="noreferrer">пост ↗</a> : null },
  ];

  return (
    <div className="decision-log view-container">
      <div className="header-row">
        <div>
          <h1><ListChecks size={22} style={{ verticalAlign: '-4px' }} /> Решения</h1>
          <p className="subtitle">Почему рой отреагировал или нет: что бот распознал (текст / расшифровка аудио / OCR фото), вердикт релевантности и причина пропуска. История с поиском и сортировкой.</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={d => d.id}
        loading={loading}
        searchText={d => `${d.agent_id || ''} ${d.channel_ref || ''} ${d.detail || ''}`}
        searchPlaceholder="🔍 Поиск по каналу, агенту или тексту…"
        emptyText="Решений пока нет. Они пишутся, когда движок проверяет посты целевых каналов."
        pageSize={25}
        toolbar={
          <>
            <select className="dl-select" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="">Все типы</option>
              <option value="relevance">Релевантность</option>
              <option value="skip">Пропуски</option>
            </select>
            <button className="btn-secondary" onClick={fetchRows}><RefreshCw size={14} /> Обновить</button>
          </>
        }
      />
    </div>
  );
};

export default DecisionLog;
