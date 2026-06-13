import React, { useState, useEffect } from 'react';
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
      setError(e instanceof Error ? e.message : 'Failed to fetch targets');
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
      setError(e instanceof Error ? e.message : 'Failed to toggle status');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this target?')) return;
    try {
      const res = await fetch(`/api/v1/landscape/${id}`, {
        method: 'DELETE',
        headers,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTargets(targets.filter(t => t.id !== id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to delete target');
    }
  };

  const handleForceSync = async () => {
    try {
      const res = await fetch('/api/v1/huginn/force-sync', {
        method: 'POST',
        headers,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      alert("Force sync signal sent to HUGINN network.");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to force sync');
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newLayers.length === 0) {
      setError('Select at least one default landscape layer.');
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
      setError(e instanceof Error ? e.message : 'Failed to save target');
    }
  };

  return (
    <div className="landscape-manager view-container">
      <div className="header-row">
        <div>
          <h1>Scraping Landscape</h1>
          <p className="subtitle">Manage target channels, feeds, and websites for intelligence gathering.</p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={handleForceSync}>Force Sync HUGINN</button>
          <button className="btn-secondary" onClick={fetchTargets}>Refresh</button>
          <button className="btn-primary" onClick={openAdd}>+ Add Target</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="data-grid-container">
        {loading ? <p>Loading targets...</p> : (
          <table className="data-grid">
            <thead>
              <tr>
                <th>ID</th>
                <th>Platform</th>
                <th>Type</th>
                <th>Default Layers</th>
                <th>Target Identifier</th>
                <th>Tags</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {targets.length === 0 ? (
                <tr><td colSpan={8} className="empty-state">No scraping targets found.</td></tr>
              ) : targets.map(target => (
                <tr key={target.id}>
                  <td>{target.id}</td>
                  <td><span className={`badge ${target.platform}`}>{target.platform}</span></td>
                  <td><span className={`badge-type`}>{target.type || 'channel'}</span></td>
                  <td>
                    <div className="tag-list">
                      {(target.default_layers || ['global']).map(l => (
                        <span key={l} className={`layer-pill layer-${l}`}>{l}</span>
                      ))}
                    </div>
                  </td>
                  <td className="font-mono">{target.target_identifier}</td>
                  <td>
                    <div className="tag-list">
                      {(target.associated_tags || []).map(tag => (
                        <span key={tag} className="tag-pill">{tag}</span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <label className="toggle-switch">
                      <input 
                        type="checkbox" 
                        checked={target.is_active} 
                        onChange={() => toggleActive(target)} 
                      />
                      <span className="slider round"></span>
                    </label>
                  </td>
                  <td>
                    <button className="btn-secondary" onClick={() => openEdit(target)} style={{ marginRight: 6 }}>Edit</button>
                    <button className="btn-danger-text" onClick={() => handleDelete(target.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={closeModal}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2>{editId === null ? 'Add Scraping Target' : 'Edit Scraping Target'}</h2>
            <form onSubmit={handleSubmit}>
              <div className="form-group">
                <label>Platform</label>
                <select value={newPlatform} onChange={e => setNewPlatform(e.target.value)}>
                  {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>Type</label>
                <select value={newType} onChange={e => setNewType(e.target.value)}>
                  {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>Default Layers <span style={{ color: '#ef4444' }}>*</span> <span style={{ color: '#94a3b8', fontWeight: 400 }}>(select one or more)</span></label>
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
                  Facts from this source are seeded with these layers; the LLM auto-classifier may add more.
                </p>
              </div>
              <div className="form-group">
                <label>Target Identifier</label>
                <input 
                  type="text" 
                  value={newTargetIdentifier} 
                  onChange={e => setNewTargetIdentifier(e.target.value)} 
                  placeholder="e.g. @username or channel_id or url"
                  required 
                />
              </div>
              <div className="form-group">
                <label>Associated Tags (comma-separated)</label>
                <input 
                  type="text" 
                  value={newTagsString} 
                  onChange={e => setNewTagsString(e.target.value)} 
                  placeholder="crypto, politics, tech"
                />
              </div>
              <div className="modal-actions">
                <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
                <button type="submit" className="btn-primary">{editId === null ? 'Save Target' : 'Update Target'}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default LandscapeManager;
