import React, { useState, useEffect } from 'react';
import { Map, RefreshCw, Plus, RotateCw } from 'lucide-react';
import { DataTable, Column } from './DataTable';
import { SidePanel } from './SidePanel';
import './LandscapeManager.css';

interface LandscapeTarget {
  id: number;
  platform: string;
  type: string;
  target_identifier: string;
  is_active: boolean;
  associated_tags: string[] | null;
  default_layers: string[];
}

interface LandscapeManagerProps {
  token: string;
}

const PLATFORMS = ['telegram', 'instagram', 'twitter', 'threads', 'facebook', 'web', 'rss'];
const TYPES = ['channel', 'feed', 'url'];
// Stage 21 — mandatory landscape layer tagged on every source.
const LAYERS = ['global', 'regional', 'state', 'city', 'personal'];

const LandscapeManager: React.FC<LandscapeManagerProps> = ({ token }) => {
  const [targets, setTargets] = useState<LandscapeTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);

  // Form State
  const [newPlatform, setNewPlatform] = useState(PLATFORMS[0]);
  const [newType, setNewType] = useState(TYPES[0]);
  const [newLayers, setNewLayers] = useState<string[]>(['global']);
  const [newTargetIdentifier, setNewTargetIdentifier] = useState('');
  const [newTagsString, setNewTagsString] = useState('');

  const toggleNewLayer = (layer: string) => {
    setNewLayers(prev => (prev.includes(layer) ? prev.filter(l => l !== layer) : [...prev, layer]));
  };

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  useEffect(() => {
    fetchTargets();
  }, []);

  const fetchTargets = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/v1/landscape/', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTargets(Array.isArray(data) ? data : []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить источники');
    } finally {
      setLoading(false);
    }
  };

  const toggleActive = async (target: LandscapeTarget) => {
    try {
      const res = await fetch(`/api/v1/landscape/${target.id}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ is_active: !target.is_active }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTargets(targets.map(t => t.id === target.id ? { ...t, is_active: !target.is_active } : t));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось переключить статус');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Удалить этот источник?')) return;
    try {
      const res = await fetch(`/api/v1/landscape/${id}`, {
        method: 'DELETE',
        headers,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTargets(targets.filter(t => t.id !== id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось удалить источник');
    }
  };

  const handleForceSync = async () => {
    try {
      const res = await fetch('/api/v1/huginn/force-sync', {
        method: 'POST',
        headers,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      alert('Сигнал принудительной синхронизации отправлен в сеть HUGINN.');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось запустить синхронизацию');
    }
  };

  const openAdd = () => {
    setEditId(null);
    setNewPlatform(PLATFORMS[0]); setNewType(TYPES[0]); setNewLayers(['global']);
    setNewTargetIdentifier(''); setNewTagsString('');
    setShowModal(true);
  };

  const openEdit = (t: LandscapeTarget) => {
    setEditId(t.id);
    setNewPlatform(t.platform); setNewType(t.type || 'channel');
    setNewLayers(t.default_layers && t.default_layers.length ? t.default_layers : ['global']);
    setNewTargetIdentifier(t.target_identifier);
    setNewTagsString((t.associated_tags || []).join(', '));
    setShowModal(true);
  };

  const closeModal = () => { setShowModal(false); setEditId(null); };

  const columns: Column<LandscapeTarget>[] = [
    { key: 'id', header: 'ID', width: '60px', sortValue: t => t.id },
    { key: 'platform', header: 'Платформа', width: '110px',
      render: t => <span className={`badge ${t.platform}`}>{t.platform}</span> },
    { key: 'type', header: 'Тип', width: '90px',
      sortValue: t => t.type || 'channel',
      render: t => <span className="badge-type">{t.type || 'channel'}</span> },
    { key: 'default_layers', header: 'Слои', sortable: false,
      render: t => (
        <div className="tag-list">
          {(t.default_layers || ['global']).map(l => <span key={l} className={`layer-pill layer-${l}`}>{l}</span>)}
        </div>
      ) },
    { key: 'target_identifier', header: 'Источник',
      render: t => <span className="font-mono">{t.target_identifier}</span> },
    { key: 'tags', header: 'Теги', sortable: false,
      render: t => (
        <div className="tag-list">
          {(t.associated_tags || []).map(tag => <span key={tag} className="tag-pill">{tag}</span>)}
        </div>
      ) },
    { key: 'is_active', header: 'Статус', width: '90px',
      sortValue: t => (t.is_active ? 1 : 0),
      render: t => (
        <label className="toggle-switch">
          <input type="checkbox" checked={t.is_active} onChange={() => toggleActive(t)} />
          <span className="slider round"></span>
        </label>
      ) },
    { key: 'actions', header: 'Действия', sortable: false, width: '150px',
      render: t => (
        <>
          <button className="btn-secondary" onClick={() => openEdit(t)} style={{ marginRight: 6 }}>Изм.</button>
          <button className="btn-danger-text" onClick={() => handleDelete(t.id)}>Удалить</button>
        </>
      ) },
  ];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newLayers.length === 0) {
      setError('Выберите хотя бы один слой.');
      return;
    }
    const tags = newTagsString.split(',').map(t => t.trim()).filter(t => t.length > 0);
    const body = {
      platform: newPlatform,
      type: newType,
      default_layers: newLayers,
      target_identifier: newTargetIdentifier,
      associated_tags: tags.length > 0 ? tags : null,
      ...(editId === null ? { is_active: true } : {}),
    };
    try {
      const res = await fetch(editId === null ? '/api/v1/landscape/' : `/api/v1/landscape/${editId}`, {
        method: editId === null ? 'POST' : 'PUT',
        headers,
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }
      closeModal();
      fetchTargets();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось сохранить источник');
    }
  };

  return (
    <div className="landscape-manager view-container">
      <div className="header-row">
        <div>
          <h1><Map size={22} style={{ verticalAlign: '-4px' }} /> Ландшафт скрапинга</h1>
          <p className="subtitle">Источники сбора данных: каналы, ленты и сайты, из которых рой собирает новости в базу знаний.</p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={handleForceSync}><RotateCw size={14} /> Синхр. HUGINN</button>
          <button className="btn-secondary" onClick={fetchTargets}><RefreshCw size={14} /> Обновить</button>
          <button className="btn-primary" onClick={openAdd}><Plus size={14} /> Добавить источник</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <DataTable
        columns={columns}
        rows={targets}
        rowKey={t => t.id}
        loading={loading}
        searchText={t => `${t.platform} ${t.type || ''} ${t.target_identifier} ${(t.associated_tags || []).join(' ')}`}
        searchPlaceholder="🔍 Поиск по источнику, платформе, типу или тегу…"
        emptyText="Источников нет. Добавьте канал, ленту или сайт кнопкой «Добавить источник»."
        pageSize={25}
      />

      <SidePanel
        open={showModal}
        title={editId === null ? 'Добавить источник' : 'Изменить источник'}
        onClose={closeModal}
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={closeModal}>Отмена</button>
            <button type="submit" form="landscape-form" className="btn-primary">{editId === null ? 'Сохранить' : 'Обновить'}</button>
          </>
        }
      >
            <form id="landscape-form" onSubmit={handleSubmit}>
              <div className="form-group">
                <label>Платформа</label>
                <select value={newPlatform} onChange={e => setNewPlatform(e.target.value)}>
                  {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>Тип</label>
                <select value={newType} onChange={e => setNewType(e.target.value)}>
                  {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>Слои по умолчанию <span style={{ color: '#ef4444' }}>*</span> <span style={{ color: '#94a3b8', fontWeight: 400 }}>(один или больше)</span></label>
                <div className="layer-checkbox-group">
                  {LAYERS.map(l => (
                    <button
                      type="button"
                      key={l}
                      className={`layer-toggle ${newLayers.includes(l) ? `active layer-${l}` : ''}`}
                      onClick={() => toggleNewLayer(l)}
                    >
                      {l}
                    </button>
                  ))}
                </div>
                <p className="help-text" style={{ fontSize: '0.8em', color: '#888', marginTop: '4px' }}>
                  Факты из этого источника получают эти слои; LLM-классификатор может добавить ещё.
                </p>
              </div>
              <div className="form-group">
                <label>Идентификатор источника</label>
                <input
                  type="text"
                  value={newTargetIdentifier}
                  onChange={e => setNewTargetIdentifier(e.target.value)}
                  placeholder="напр. @username, channel_id или url"
                  required
                />
              </div>
              <div className="form-group">
                <label>Теги (через запятую)</label>
                <input
                  type="text"
                  value={newTagsString}
                  onChange={e => setNewTagsString(e.target.value)}
                  placeholder="крипто, политика, технологии"
                />
              </div>
            </form>
      </SidePanel>
    </div>
  );
};

export default LandscapeManager;
