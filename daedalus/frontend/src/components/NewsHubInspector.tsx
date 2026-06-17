import React, { useState, useEffect } from 'react';
import { Radio, Trash2, Edit2, Play, Pause, ShieldAlert, RefreshCw } from 'lucide-react';
import { DataTable, Column } from './DataTable';
import { SidePanel } from './SidePanel';
import './NewsHubInspector.css';

interface CapturedEvent {
  id: number;
  event_id: string;
  source_platform: string;
  source_target: string;
  post_id: string;
  text_content: string | null;
  media_type: string | null;
  media_path: string | null;
  layers: Record<string, unknown>;
  timestamp: number;
  status: string;
}

interface NewsHubInspectorProps {
  token: string;
}

const STATUS_LABEL: Record<string, string> = {
  pending: 'в очереди', approved: 'одобрено', rejected: 'отклонено', Processed: 'обработано',
};

// The stored layers object mixes booleans (global/region/state/city) with a
// personal_tags array — surface only the truthy ones as tags.
const activeLayers = (layers: Record<string, unknown>): string[] => {
  if (!layers) return [];
  const out: string[] = [];
  for (const [k, v] of Object.entries(layers)) {
    if (k === 'personal_tags' && Array.isArray(v)) out.push(...(v as string[]));
    else if (v === true) out.push(k);
  }
  return out;
};

const NewsHubInspector: React.FC<NewsHubInspectorProps> = ({ token }) => {
  const [events, setEvents] = useState<CapturedEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [isLive, setIsLive] = useState(true);
  const [statusFilter, setStatusFilter] = useState('');

  // Edit Modal State
  const [editingEvent, setEditingEvent] = useState<CapturedEvent | null>(null);
  const [editForm, setEditForm] = useState<{
    text_content: string;
    status: string;
    layers: Record<string, boolean>;
  }>({ text_content: '', status: 'pending', layers: {} });

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  useEffect(() => {
    fetchEvents();
    let interval: ReturnType<typeof setInterval>;
    if (isLive) interval = setInterval(fetchEvents, 5000);
    return () => clearInterval(interval);
  }, [isLive]);

  const fetchEvents = async () => {
    try {
      const res = await fetch('/api/v1/huginn/captured-events?limit=200', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(data.events || []);
      setError('');
    } catch (e: unknown) {
      console.error(e);
      if (events.length === 0) setError('Не удалось получить телеметрию из HUGINN.');
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateStatus = async (id: number, status: string) => {
    try {
      const res = await fetch(`/api/v1/huginn/captured-events/${id}`, {
        method: 'PUT', headers, body: JSON.stringify({ status }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEvents(events.map(e => e.id === id ? { ...e, status } : e));
    } catch (e: unknown) {
      console.error(e);
    }
  };

  const handleSaveEdit = async () => {
    if (!editingEvent) return;
    try {
      const payload = {
        text_content: editForm.text_content,
        status: editForm.status,
        layers: editForm.layers,
      };
      const res = await fetch(`/api/v1/huginn/captured-events/${editingEvent.id}`, {
        method: 'PUT', headers, body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEvents(events.map(e => e.id === editingEvent.id ? { ...e, ...payload } : e));
      setEditingEvent(null);
    } catch (e: unknown) {
      console.error(e);
      alert('Не удалось сохранить изменения события.');
    }
  };

  const openEdit = (e: CapturedEvent) => {
    setEditingEvent(e);
    setEditForm({
      text_content: e.text_content || '',
      status: e.status,
      layers: (e.layers as Record<string, boolean>) || { global: false, region: false, state: false, city: false },
    });
  };

  const toggleLayer = (layerName: string) => {
    setEditForm(prev => ({ ...prev, layers: { ...prev.layers, [layerName]: !prev.layers[layerName] } }));
  };

  const shown = statusFilter ? events.filter(e => e.status === statusFilter) : events;

  const columns: Column<CapturedEvent>[] = [
    { key: 'source_platform', header: 'Платформа', width: '110px',
      render: e => <span className={`platform-badge ${e.source_platform}`}>{e.source_platform}</span> },
    { key: 'source_target', header: 'Источник', width: '180px',
      sortValue: e => e.source_target,
      render: e => <span className="event-target" title={e.source_target}>{e.source_target}</span> },
    { key: 'text_content', header: 'Текст', sortable: false,
      render: e => e.text_content
        ? <span className="nh-text">{e.text_content.slice(0, 160)}{e.text_content.length > 160 ? '…' : ''}</span>
        : <span className="event-media-only">[только медиа: {e.media_type || '—'}]</span> },
    { key: 'layers', header: 'Слои', sortable: false, width: '160px',
      render: e => (
        <div className="event-layers">
          {activeLayers(e.layers).map(l => <span key={l} className="layer-tag">{l}</span>)}
        </div>
      ) },
    { key: 'timestamp', header: 'Время', width: '150px',
      sortValue: e => e.timestamp,
      render: e => <span className="event-time">{new Date(e.timestamp * 1000).toLocaleString('ru-RU')}</span> },
    { key: 'status', header: 'Статус', width: '110px',
      sortValue: e => e.status,
      render: e => <span className={`status-pill ${e.status}`}>{STATUS_LABEL[e.status] || e.status}</span> },
    { key: 'actions', header: 'Действия', sortable: false, width: '120px',
      render: e => (
        <div className="event-actions">
          <button onClick={() => openEdit(e)} title="Изменить"><Edit2 size={15} /></button>
          <button onClick={() => handleUpdateStatus(e.id, 'rejected')} className="btn-reject" title="Отклонить"><Trash2 size={15} /></button>
          <button onClick={() => handleUpdateStatus(e.id, 'approved')} className="btn-approve" title="Одобрить"><ShieldAlert size={15} /></button>
        </div>
      ) },
  ];

  return (
    <div className="newshub-inspector view-container">
      <div className="header-row">
        <div>
          <h1><Radio size={22} style={{ verticalAlign: '-4px' }} /> Центр HUGINN</h1>
          <p className="subtitle">Перехваченные события сбора новостей: что попало в очередь к ORPHEUS. Можно изменить текст, маршрут (слои) и статус, одобрить или отклонить.</p>
        </div>
        <div className="header-actions">
          <button className={`btn-secondary ${isLive ? 'active-pulse' : ''}`} onClick={() => setIsLive(!isLive)}>
            {isLive ? <><Pause size={15} /> Пауза</> : <><Play size={15} /> Live</>}
          </button>
          <button className="btn-secondary" onClick={fetchEvents}><RefreshCw size={14} /> Обновить</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <DataTable
        columns={columns}
        rows={shown}
        rowKey={e => e.id}
        loading={loading}
        searchText={e => `${e.source_platform} ${e.source_target} ${e.text_content || ''}`}
        searchPlaceholder="🔍 Поиск по тексту, источнику или платформе…"
        emptyText="Событий пока нет. Проверьте статус HUGINN или источники в «Ландшафте скрапинга»."
        pageSize={25}
        toolbar={
          <select className="nh-select" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
            <option value="">Все статусы</option>
            <option value="pending">В очереди</option>
            <option value="Processed">Обработано</option>
            <option value="approved">Одобрено</option>
            <option value="rejected">Отклонено</option>
          </select>
        }
      />

      {/* Edit side panel */}
      {editingEvent && (
        <SidePanel
          open
          title="Изменить событие"
          subtitle={`ID: ${editingEvent.event_id}`}
          onClose={() => setEditingEvent(null)}
          footer={
            <>
              <button className="btn-secondary" onClick={() => setEditingEvent(null)}>Отмена</button>
              <button className="btn-primary" onClick={handleSaveEdit}>Сохранить</button>
            </>
          }
        >
          <div className="form-group">
            <label>Текст (переопределить)</label>
            <textarea
              rows={5}
              value={editForm.text_content}
              onChange={e => setEditForm({ ...editForm, text_content: e.target.value })}
              className="form-control"
            />
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Статус маршрутизации</label>
              <select
                className="form-control"
                value={editForm.status}
                onChange={e => setEditForm({ ...editForm, status: e.target.value })}
              >
                <option value="pending">В очереди (передать ORPHEUS)</option>
                <option value="approved">Одобрено (приоритет)</option>
                <option value="rejected">Отклонено (отбросить)</option>
              </select>
            </div>
          </div>

          <div className="form-group">
            <label>Слои маршрутизации</label>
            <div className="layer-checkboxes">
              {['global', 'region', 'state', 'city', 'personal'].map(layer => (
                <label key={layer} className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={!!editForm.layers[layer]}
                    onChange={() => toggleLayer(layer)}
                  />
                  <span>{layer}</span>
                </label>
              ))}
            </div>
          </div>
        </SidePanel>
      )}
    </div>
  );
};

export default NewsHubInspector;
