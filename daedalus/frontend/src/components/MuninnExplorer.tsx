import React, { useEffect, useState } from 'react';
import { Brain, Plus, Trash2, RefreshCw, Layers } from 'lucide-react';
import './MuninnExplorer.css';

interface KnowledgeFact {
  id: number;
  content: string;
  source_url: string | null;
  landscape_layers: string[];
  categories: string[];
  tags: string[];
  sources: string[] | null;
  source_count: number;
  timestamp: number;
  created_at: string;
  updated_at: string;
}

interface MuninnExplorerProps {
  token: string;
}

const LAYERS = ['global', 'regional', 'state', 'city', 'personal'];

const LAYER_COLORS: Record<string, string> = {
  global: '#3b82f6',
  regional: '#8b5cf6',
  state: '#ec4899',
  city: '#f59e0b',
  personal: '#10b981',
};

const MuninnExplorer: React.FC<MuninnExplorerProps> = ({ token }) => {
  const [facts, setFacts] = useState<KnowledgeFact[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [layerFilter, setLayerFilter] = useState<string>('');

  // Manual-injection form state.
  const [showInject, setShowInject] = useState(false);
  const [injecting, setInjecting] = useState(false);
  const [injectContent, setInjectContent] = useState('');
  const [injectLayers, setInjectLayers] = useState<string[]>(['global']);
  const [injectSource, setInjectSource] = useState('');

  const toggleInjectLayer = (layer: string) => {
    setInjectLayers(prev => (prev.includes(layer) ? prev.filter(l => l !== layer) : [...prev, layer]));
  };

  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };

  useEffect(() => {
    fetchFacts();
    fetchStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layerFilter]);

  const fetchFacts = async () => {
    setLoading(true);
    setError('');
    try {
      const qs = layerFilter ? `?layer=${encodeURIComponent(layerFilter)}` : '';
      const res = await fetch(`/api/v1/knowledge/facts${qs}`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setFacts(Array.isArray(data.facts) ? data.facts : []);
      setTotal(data.total ?? 0);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load knowledge facts');
    } finally {
      setLoading(false);
    }
  };

  const fetchStats = async () => {
    try {
      const res = await fetch('/api/v1/knowledge/stats', { headers });
      if (res.ok) {
        const data = await res.json();
        setStats(data.by_layer || {});
      }
    } catch {
      /* stats are non-critical */
    }
  };

  const handleInject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!injectContent.trim()) return;
    if (injectLayers.length === 0) {
      setError('Select at least one landscape layer.');
      return;
    }
    setInjecting(true);
    setError('');
    try {
      const res = await fetch('/api/v1/knowledge/facts/inject', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          content: injectContent,
          layers: injectLayers,
          source_url: injectSource.trim() || 'manual://operator-injection',
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
      setShowInject(false);
      setInjectContent('');
      setInjectSource('');
      setInjectLayers(['global']);
      fetchFacts();
      fetchStats();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to inject fact');
    } finally {
      setInjecting(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Purge this knowledge fact from the swarm memory?')) return;
    try {
      const res = await fetch(`/api/v1/knowledge/facts/${id}`, { method: 'DELETE', headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFacts(facts.filter(f => f.id !== id));
      fetchStats();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to delete fact');
    }
  };

  return (
    <div className="muninn-explorer view-container">
      <div className="header-row">
        <div>
          <h1><Brain size={22} style={{ verticalAlign: '-4px' }} /> Muninn Memory Explorer</h1>
          <p className="subtitle">Clustered KnowledgeFacts — the swarm's deduplicated semantic memory, tagged by cognitive layer.</p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={() => { fetchFacts(); fetchStats(); }}>
            <RefreshCw size={14} /> Refresh
          </button>
          <button className="btn-primary" onClick={() => setShowInject(true)}>
            <Plus size={14} /> Manually Inject Fact
          </button>
        </div>
      </div>

      {/* Layer cluster stat cards */}
      <div className="mx-stat-row">
        <div className={`mx-stat-card ${layerFilter === '' ? 'active' : ''}`} onClick={() => setLayerFilter('')}>
          <Layers size={16} />
          <div className="mx-stat-num">{total}</div>
          <div className="mx-stat-label">All Layers</div>
        </div>
        {LAYERS.map(layer => (
          <div
            key={layer}
            className={`mx-stat-card ${layerFilter === layer ? 'active' : ''}`}
            onClick={() => setLayerFilter(layer)}
            style={{ borderTopColor: LAYER_COLORS[layer] }}
          >
            <div className="mx-stat-num">{stats[layer] ?? 0}</div>
            <div className="mx-stat-label">{layer}</div>
          </div>
        ))}
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="data-grid-container">
        {loading ? <p>Loading memory clusters…</p> : (
          <table className="data-grid">
            <thead>
              <tr>
                <th>ID</th>
                <th>Layers</th>
                <th>Fact (Cluster)</th>
                <th>Categories / Tags</th>
                <th>Sources</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {facts.length === 0 ? (
                <tr><td colSpan={7} className="empty-state">No knowledge facts stored yet. Scrape sources or inject one manually.</td></tr>
              ) : facts.map(fact => (
                <tr key={fact.id}>
                  <td>{fact.id}</td>
                  <td>
                    <div className="mx-chip-row">
                      {(fact.landscape_layers || []).map(l => (
                        <span key={l} className="layer-pill" style={{ background: LAYER_COLORS[l] || '#475569' }}>{l}</span>
                      ))}
                    </div>
                  </td>
                  <td className="mx-fact-content">{fact.content}</td>
                  <td>
                    <div className="mx-chip-row">
                      {(fact.categories || []).map(c => <span key={c} className="mx-chip mx-chip-cat">{c}</span>)}
                      {(fact.tags || []).map(t => <span key={t} className="mx-chip mx-chip-tag">#{t}</span>)}
                    </div>
                  </td>
                  <td>
                    <span className="mx-cluster-count" title={(fact.sources || []).join('\n')}>
                      ×{fact.source_count}
                    </span>
                  </td>
                  <td className="mx-date">{new Date(fact.updated_at).toLocaleString()}</td>
                  <td>
                    <button className="btn-icon text-danger" onClick={() => handleDelete(fact.id)} title="Purge fact">
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showInject && (
        <div className="modal-overlay" onClick={() => setShowInject(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2>Manually Inject Fact into Memory</h2>
            <p className="help-text">Daedalus auto-classifies (categories/tags via <code>qwen2.5:3b</code>), embeds via <code>nomic-embed-text</code> and clusters it (cosine&nbsp;&gt;&nbsp;0.85 merges into an existing fact).</p>
            <form onSubmit={handleInject}>
              <div className="form-group">
                <label>Fact Content <span style={{ color: '#ef4444' }}>*</span></label>
                <textarea
                  rows={5}
                  value={injectContent}
                  onChange={e => setInjectContent(e.target.value)}
                  placeholder="e.g. The Tashkent metro extended its Yunusabad line on 2026-06-01."
                  required
                />
              </div>
              <div className="form-group">
                <label>Landscape Layers <span style={{ color: '#ef4444' }}>*</span> <span style={{ color: '#94a3b8', fontWeight: 400 }}>(select one or more)</span></label>
                <div className="mx-chip-select">
                  {LAYERS.map(l => (
                    <button
                      type="button"
                      key={l}
                      className={`mx-layer-toggle ${injectLayers.includes(l) ? 'active' : ''}`}
                      style={injectLayers.includes(l) ? { background: LAYER_COLORS[l], borderColor: LAYER_COLORS[l] } : undefined}
                      onClick={() => toggleInjectLayer(l)}
                    >
                      {l}
                    </button>
                  ))}
                </div>
                <p className="help-text" style={{ fontSize: '0.8em', marginTop: '4px' }}>The LLM may add more layers automatically based on the text.</p>
              </div>
              <div className="form-group">
                <label>Source URL (optional)</label>
                <input
                  type="text"
                  value={injectSource}
                  onChange={e => setInjectSource(e.target.value)}
                  placeholder="https://… or leave blank"
                />
              </div>
              <div className="modal-actions">
                <button type="button" className="btn-secondary" onClick={() => setShowInject(false)}>Cancel</button>
                <button type="submit" className="btn-primary" disabled={injecting}>
                  {injecting ? 'Injecting…' : 'Inject into Swarm Memory'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default MuninnExplorer;
