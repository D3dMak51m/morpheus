import React, { useState, useEffect } from 'react';
import './LandscapeManager.css';

interface LandscapeTarget {
  id: number;
  platform: string;
  type: string;
  target_identifier: string;
  is_active: boolean;
  associated_tags: string[] | null;
}

interface LandscapeManagerProps {
  token: string;
}

const PLATFORMS = ['telegram', 'instagram', 'twitter', 'threads', 'facebook', 'web'];
const TYPES = ['channel', 'feed', 'url'];

const LandscapeManager: React.FC<LandscapeManagerProps> = ({ token }) => {
  const [targets, setTargets] = useState<LandscapeTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  
  // Form State
  const [newPlatform, setNewPlatform] = useState(PLATFORMS[0]);
  const [newType, setNewType] = useState(TYPES[0]);
  const [newTargetIdentifier, setNewTargetIdentifier] = useState('');
  const [newTagsString, setNewTagsString] = useState('');

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

  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const tags = newTagsString.split(',').map(t => t.trim()).filter(t => t.length > 0);
    try {
      const res = await fetch('/api/v1/landscape/', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          platform: newPlatform,
          type: newType,
          target_identifier: newTargetIdentifier,
          is_active: true,
          associated_tags: tags.length > 0 ? tags : null
        })
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }
      setShowModal(false);
      setNewTargetIdentifier('');
      setNewTagsString('');
      fetchTargets();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to add target');
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
          <button className="btn-primary" onClick={() => setShowModal(true)}>+ Add Target</button>
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
                <th>Target Identifier</th>
                <th>Tags</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {targets.length === 0 ? (
                <tr><td colSpan={7} className="empty-state">No scraping targets found.</td></tr>
              ) : targets.map(target => (
                <tr key={target.id}>
                  <td>{target.id}</td>
                  <td><span className={`badge ${target.platform}`}>{target.platform}</span></td>
                  <td><span className={`badge-type`}>{target.type || 'channel'}</span></td>
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
                    <button className="btn-danger-text" onClick={() => handleDelete(target.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2>Add Scraping Target</h2>
            <form onSubmit={handleAddSubmit}>
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
                <button type="button" className="btn-secondary" onClick={() => setShowModal(false)}>Cancel</button>
                <button type="submit" className="btn-primary">Save Target</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default LandscapeManager;
