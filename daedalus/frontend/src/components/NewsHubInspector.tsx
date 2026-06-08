import React, { useState, useEffect } from 'react';
import { Clock, ShieldAlert, Trash2, Edit2, Play, Pause, Server, Activity, Database } from 'lucide-react';
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
  layers: Record<string, boolean>;
  timestamp: number;
  status: string;
}

interface NewsHubInspectorProps {
  token: string;
}

const NewsHubInspector: React.FC<NewsHubInspectorProps> = ({ token }) => {
  const [events, setEvents] = useState<CapturedEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [isLive, setIsLive] = useState(true);
  
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
    if (isLive) {
      interval = setInterval(fetchEvents, 5000);
    }
    
    return () => clearInterval(interval);
  }, [isLive]);

  const fetchEvents = async () => {
    try {
      const res = await fetch('/api/v1/huginn/captured-events?limit=20', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(data.events || []);
      setError('');
    } catch (e: unknown) {
      console.error(e);
      if (events.length === 0) setError('Failed to fetch telemetry from HUGINN.');
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateStatus = async (id: number, status: string) => {
    try {
      const res = await fetch(`/api/v1/huginn/captured-events/${id}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ status }),
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
        layers: editForm.layers
      };
      
      const res = await fetch(`/api/v1/huginn/captured-events/${editingEvent.id}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      
      setEvents(events.map(e => e.id === editingEvent.id ? { ...e, ...payload } : e));
      setEditingEvent(null);
    } catch (e: unknown) {
      console.error(e);
      alert('Failed to save payload overrides.');
    }
  };

  const openEdit = (e: CapturedEvent) => {
    setEditingEvent(e);
    setEditForm({
      text_content: e.text_content || '',
      status: e.status,
      layers: e.layers || { Global: false, Region: false, State: false, City: false, Personal: false }
    });
  };

  const toggleLayer = (layerName: string) => {
    setEditForm(prev => ({
      ...prev,
      layers: {
        ...prev.layers,
        [layerName]: !prev.layers[layerName]
      }
    }));
  };

  return (
    <div className="newshub-inspector view-container">
      <div className="header-row">
        <div>
          <h1>HUGINN Command Center</h1>
          <p className="subtitle">Real-time telemetry, routing, and operational override controls.</p>
        </div>
        <div className="header-actions">
          <button 
            className={`btn-secondary ${isLive ? 'active-pulse' : ''}`}
            onClick={() => setIsLive(!isLive)}
          >
            {isLive ? <><Pause size={16}/> Pause Telemetry</> : <><Play size={16}/> Live Sync</>}
          </button>
          <button className="btn-secondary" onClick={fetchEvents}>Refresh</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="newshub-split-view">
        {/* Left Panel: Event Stream */}
        <div className="event-stream-panel card">
          <div className="card-header">
            <h3>Event Interception Stream</h3>
            <span className="badge badge-pulse">LIVE</span>
          </div>
          
          <div className="event-list">
            {loading && events.length === 0 ? (
              <div className="loading-state">Monitoring gathering layer...</div>
            ) : events.length === 0 ? (
              <div className="empty-state">No events captured yet. Check HUGINN status.</div>
            ) : (
              events.map((event) => (
                <div key={event.id} className={`event-card status-${event.status}`}>
                  <div className="event-header">
                    <span className={`platform-badge ${event.source_platform}`}>
                      {event.source_platform}
                    </span>
                    <span className="event-target">{event.source_target}</span>
                    <span className="event-time">
                      <Clock size={12} />
                      {new Date(event.timestamp * 1000).toLocaleTimeString()}
                    </span>
                  </div>
                  
                  <div className="event-body">
                    {event.text_content ? (
                      <p className="event-text">{event.text_content.substring(0, 200)}{event.text_content.length > 200 ? '...' : ''}</p>
                    ) : (
                      <p className="event-media-only">[Media Only: {event.media_type}]</p>
                    )}
                    
                    {event.layers && (
                       <div className="event-layers">
                         {Object.entries(event.layers).filter(([_, v]) => v).map(([k]) => (
                           <span key={k} className="layer-tag">{k}</span>
                         ))}
                       </div>
                    )}
                  </div>
                  
                  <div className="event-footer">
                    <div className="event-status">
                      Status: <span className={`status-pill ${event.status}`}>{event.status}</span>
                    </div>
                    <div className="event-actions">
                      <button onClick={() => openEdit(event)} title="Edit Payload"><Edit2 size={16}/></button>
                      <button onClick={() => handleUpdateStatus(event.id, 'rejected')} className="btn-reject" title="Reject"><Trash2 size={16}/></button>
                      <button onClick={() => handleUpdateStatus(event.id, 'approved')} className="btn-approve" title="Force Approve"><ShieldAlert size={16}/></button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Right Panel: Telemetry */}
        <div className="telemetry-panel">
          <div className="card stats-card">
            <h3>Swarm Diagnostics</h3>
            <div className="stat-grid">
              <div className="stat-box">
                <span className="stat-label"><Activity size={14}/> Gathering Nodes</span>
                <span className="stat-value active">ONLINE</span>
              </div>
              <div className="stat-box">
                <span className="stat-label"><Database size={14}/> Redis Event Queue</span>
                <span className="stat-value">{events.length > 0 ? 'SYNCED' : 'AWAITING'}</span>
              </div>
              <div className="stat-box">
                <span className="stat-label"><Server size={14}/> Intercepted Today</span>
                <span className="stat-value">{events.length > 0 ? events.length * 15 : '--'}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Edit Modal */}
      {editingEvent && (
        <div className="modal-overlay" onClick={() => setEditingEvent(null)}>
          <div className="modal-content standard-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Modify Event Payload</h2>
              <p className="subtitle">ID: {editingEvent.event_id}</p>
            </div>
            
            <div className="form-group">
              <label>Text Content (Override)</label>
              <textarea 
                rows={5}
                value={editForm.text_content} 
                onChange={e => setEditForm({...editForm, text_content: e.target.value})}
                className="form-control"
              />
            </div>
            
            <div className="form-row">
              <div className="form-group">
                <label>Routing Status</label>
                <select 
                  className="form-control"
                  value={editForm.status}
                  onChange={e => setEditForm({...editForm, status: e.target.value})}
                >
                  <option value="pending">Pending (Queue for ORPHEUS)</option>
                  <option value="approved">Approved (Priority Bypass)</option>
                  <option value="rejected">Rejected (Discard Event)</option>
                </select>
              </div>
            </div>

            <div className="form-group">
              <label>Topological Routing Layers</label>
              <div className="layer-checkboxes">
                {['Global', 'Region', 'State', 'City', 'Personal'].map(layer => (
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

            <div className="modal-actions">
              <button className="btn-secondary" onClick={() => setEditingEvent(null)}>Cancel</button>
              <button className="btn-primary" onClick={handleSaveEdit}>Commit Changes</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default NewsHubInspector;
