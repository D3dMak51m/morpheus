import React, { useState, useEffect } from 'react';
import './ActivityStream.css';

interface ActivityLog {
  id: number;
  agent_id: string;
  platform: string;
  action_type: string;
  target_url: string;
  text_content: string | null;
  status: string;
  created_at: string;
}

interface ActivityStreamProps {
  token: string;
}

const PLATFORMS = ['telegram', 'instagram', 'twitter', 'threads'];

const ActivityStream: React.FC<ActivityStreamProps> = ({ token }) => {
  const [logs, setLogs] = useState<ActivityLog[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Filters
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [agentIdFilter, setAgentIdFilter] = useState('');

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  const fetchLogs = async () => {
    try {
      let url = '/api/v1/analytics/stream?limit=50';
      if (agentIdFilter) url += `&agent_id=${encodeURIComponent(agentIdFilter)}`;
      // The API only supports single platform filter right now, so we will filter locally if multiple are selected,
      // or we can just send the first one if length === 1
      if (selectedPlatforms.length === 1) {
        url += `&platform=${encodeURIComponent(selectedPlatforms[0])}`;
      }

      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      
      let fetchedLogs = data.logs || [];
      if (selectedPlatforms.length > 1) {
        fetchedLogs = fetchedLogs.filter((l: ActivityLog) => selectedPlatforms.includes(l.platform));
      }
      
      setLogs(fetchedLogs);
      setTotal(data.total || 0);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить журнал активности');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [selectedPlatforms, agentIdFilter]);

  const togglePlatform = (p: string) => {
    setSelectedPlatforms(prev => 
      prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]
    );
  };

  return (
    <div className="activity-stream view-container">
      <div className="header-row">
        <div>
          <h1>Журнал активности</h1>
          <p className="subtitle">Логи действий всех воркеров роя в реальном времени. (всего записей: {total})</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="filters-section">
        <div className="filter-group">
          <label>ID агента:</label>
          <input
            type="text"
            placeholder="напр. clone_alpha_91eea738"
            value={agentIdFilter}
            onChange={e => setAgentIdFilter(e.target.value)}
            className="agent-input"
          />
        </div>
        <div className="filter-group">
          <label>Платформы:</label>
          <div className="pill-group">
            {PLATFORMS.map(p => (
              <button 
                key={p} 
                className={`pill ${selectedPlatforms.includes(p) ? 'selected' : ''}`}
                onClick={() => togglePlatform(p)}
              >
                {p}
              </button>
            ))}
            {selectedPlatforms.length > 0 && (
              <button className="btn-danger-text" onClick={() => setSelectedPlatforms([])}>Сброс</button>
            )}
          </div>
        </div>
      </div>

      <div className="stream-container">
        {loading && logs.length === 0 ? <p>Загрузка ленты…</p> : (
          logs.length === 0 ? <p className="empty-state">Записей по заданным фильтрам не найдено.</p> : (
            <div className="log-list">
              {logs.map(log => (
                <div key={log.id} className="log-card">
                  <div className="log-header">
                    <span className={`status-pill ${log.status.toLowerCase()}`}>{log.status}</span>
                    <span className="timestamp">{new Date(log.created_at).toLocaleString()}</span>
                  </div>
                  <div className="log-body">
                    <div className="log-meta">
                      <span className="font-mono">Агент {log.agent_id}</span>
                      <span className={`badge ${log.platform}`}>{log.platform}</span>
                      <span className="action-type">{log.action_type.toUpperCase()}</span>
                    </div>
                    <div className="log-target">
                      <a href={log.target_url} target="_blank" rel="noreferrer">{log.target_url}</a>
                    </div>
                    {log.text_content && (
                      <div className="log-text">
                        "{log.text_content}"
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
};

export default ActivityStream;
