import React, { useEffect, useState, useCallback } from 'react';
import { Brain, Plus, Trash2, RefreshCw, Layers } from 'lucide-react';
import { SidePanel } from './SidePanel';
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
  global: '#3b82f6', regional: '#8b5cf6', state: '#ec4899', city: '#f59e0b', personal: '#10b981',
};
const PAGE = 50;

// Human-friendly source label (Telegram channel / website / manual).
function formatSource(url: string | null): { icon: string; label: string } {
  if (!url) return { icon: '•', label: '—' };
  if (url.startsWith('manual://')) return { icon: '✍️', label: 'ручной ввод' };
  const tg = url.match(/t\.me\/(?:c\/)?@?([A-Za-z0-9_]+)/);
  if (tg) return { icon: '📢', label: '@' + tg[1] };
  if (url.startsWith('@')) return { icon: '📢', label: url };
  try {
    return { icon: '📰', label: new URL(url).hostname.replace(/^www\./, '') };
  } catch {
    return { icon: '🔗', label: url.slice(0, 32) };
  }
}

const MuninnExplorer: React.FC<MuninnExplorerProps> = ({ token }) => {
  const [facts, setFacts] = useState<KnowledgeFact[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [layerFilter, setLayerFilter] = useState<string>('');
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(PAGE);

  const [showInject, setShowInject] = useState(false);
  const [injecting, setInjecting] = useState(false);
  const [injectContent, setInjectContent] = useState('');
  const [injectLayers, setInjectLayers] = useState<string[]>(['global']);
  const [injectSource, setInjectSource] = useState('');

  const toggleInjectLayer = (layer: string) =>
    setInjectLayers(prev => (prev.includes(layer) ? prev.filter(l => l !== layer) : [...prev, layer]));

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const fetchFacts = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: String(limit) });
      if (layerFilter) params.set('layer', layerFilter);
      if (query.trim()) params.set('q', query.trim());
      const res = await fetch(`/api/v1/knowledge/facts?${params}`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setFacts(Array.isArray(data.facts) ? data.facts : []);
      setTotal(data.total ?? 0);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить факты');
    } finally {
      setLoading(false);
    }
  }, [layerFilter, query, limit, token]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/knowledge/stats', { headers });
      if (res.ok) setStats((await res.json()).by_layer || {});
    } catch { /* non-critical */ }
  }, [token]);

  // Debounce search; refetch on layer/limit/query.
  useEffect(() => {
    const t = setTimeout(fetchFacts, query ? 300 : 0);
    return () => clearTimeout(t);
  }, [fetchFacts]);
  useEffect(() => { fetchStats(); }, [fetchStats]);

  const handleInject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!injectContent.trim()) return;
    if (injectLayers.length === 0) { setError('Выберите хотя бы один слой.'); return; }
    setInjecting(true);
    setError('');
    try {
      const res = await fetch('/api/v1/knowledge/facts/inject', {
        method: 'POST', headers,
        body: JSON.stringify({ content: injectContent, layers: injectLayers, source_url: injectSource.trim() || 'manual://operator-injection' }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
      setShowInject(false); setInjectContent(''); setInjectSource(''); setInjectLayers(['global']);
      fetchFacts(); fetchStats();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось добавить факт');
    } finally {
      setInjecting(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Удалить этот факт из памяти роя?')) return;
    try {
      const res = await fetch(`/api/v1/knowledge/facts/${id}`, { method: 'DELETE', headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFacts(facts.filter(f => f.id !== id));
      fetchStats();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось удалить факт');
    }
  };

  return (
    <div className="muninn-explorer view-container">
      <div className="header-row">
        <div>
          <h1><Brain size={22} style={{ verticalAlign: '-4px' }} /> Знания роя</h1>
          <p className="subtitle">Что рой узнал из новостей и источников — дедуплицированные факты (RAG), которыми пользуется ORPHEUS при ответах.</p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={() => { fetchFacts(); fetchStats(); }}>
            <RefreshCw size={14} /> Обновить
          </button>
          <button className="btn-primary" onClick={() => setShowInject(true)}>
            <Plus size={14} /> Добавить факт
          </button>
        </div>
      </div>

      <div className="mx-stat-row">
        <div className={`mx-stat-card ${layerFilter === '' ? 'active' : ''}`} onClick={() => { setLayerFilter(''); setLimit(PAGE); }}>
          <Layers size={16} />
          <div className="mx-stat-num">{stats && Object.keys(stats).length ? (total) : total}</div>
          <div className="mx-stat-label">все слои</div>
        </div>
        {LAYERS.map(layer => (
          <div key={layer} className={`mx-stat-card ${layerFilter === layer ? 'active' : ''}`}
            onClick={() => { setLayerFilter(layer); setLimit(PAGE); }} style={{ borderTopColor: LAYER_COLORS[layer] }}>
            <div className="mx-stat-num">{stats[layer] ?? 0}</div>
            <div className="mx-stat-label">{layer}</div>
          </div>
        ))}
      </div>

      <div className="mx-searchbar">
        <input
          className="mx-search"
          placeholder="🔍 Поиск по тексту факта или источнику (напр. kunuz, транспорт)…"
          value={query}
          onChange={e => { setQuery(e.target.value); setLimit(PAGE); }}
        />
        <span className="mx-result-count">{total} факт(ов){layerFilter ? ` · слой: ${layerFilter}` : ''}</span>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="data-grid-container">
        {loading && facts.length === 0 ? <p>Загрузка…</p> : (
          <table className="data-grid">
            <thead>
              <tr>
                <th>ID</th>
                <th>Слои</th>
                <th>Факт</th>
                <th>Категории / теги</th>
                <th>Источник</th>
                <th>Обновлён</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {facts.length === 0 ? (
                <tr><td colSpan={7} className="empty-state">Факты не найдены. Отметьте каналы как «новости» или добавьте факт вручную.</td></tr>
              ) : facts.map(fact => {
                const src = formatSource(fact.source_url);
                return (
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
                      <span className="mx-source" title={fact.source_url || ''}>{src.icon} {src.label}</span>
                      {fact.source_count > 1 && (
                        <span className="mx-cluster-count" title={(fact.sources || []).join('\n')}> ×{fact.source_count}</span>
                      )}
                    </td>
                    <td className="mx-date">{new Date(fact.updated_at).toLocaleString('ru-RU')}</td>
                    <td>
                      <button className="btn-icon text-danger" onClick={() => handleDelete(fact.id)} title="Удалить факт">
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {facts.length < total && (
          <div className="mx-loadmore">
            <button className="btn-secondary" onClick={() => setLimit(l => l + PAGE)}>
              Показать ещё ({total - facts.length})
            </button>
          </div>
        )}
      </div>

      <SidePanel
        open={showInject}
        title="Добавить факт в память"
        onClose={() => setShowInject(false)}
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setShowInject(false)}>Отмена</button>
            <button type="submit" form="inject-fact-form" className="btn-primary" disabled={injecting}>
              {injecting ? 'Добавление…' : 'Добавить в память'}
            </button>
          </>
        }
      >
            <p className="help-text">Daedalus авто-классифицирует (категории/теги через <code>qwen2.5:3b</code>), строит эмбеддинг (<code>nomic-embed-text</code>) и кластеризует (близость&nbsp;&gt;&nbsp;0.85 — слияние с существующим фактом).</p>
            <form id="inject-fact-form" onSubmit={handleInject}>
              <div className="form-group">
                <label>Текст факта <span style={{ color: '#ef4444' }}>*</span></label>
                <textarea rows={5} value={injectContent} onChange={e => setInjectContent(e.target.value)}
                  placeholder="напр. Ташкентское метро продлило Юнусабадскую линию 2026-06-01." required />
              </div>
              <div className="form-group">
                <label>Слои <span style={{ color: '#ef4444' }}>*</span> <span style={{ color: '#94a3b8', fontWeight: 400 }}>(один или больше)</span></label>
                <div className="mx-chip-select">
                  {LAYERS.map(l => (
                    <button type="button" key={l} className={`mx-layer-toggle ${injectLayers.includes(l) ? 'active' : ''}`}
                      style={injectLayers.includes(l) ? { background: LAYER_COLORS[l], borderColor: LAYER_COLORS[l] } : undefined}
                      onClick={() => toggleInjectLayer(l)}>{l}</button>
                  ))}
                </div>
                <p className="help-text" style={{ fontSize: '0.8em', marginTop: '4px' }}>LLM может добавить слои автоматически по тексту.</p>
              </div>
              <div className="form-group">
                <label>Источник (необязательно)</label>
                <input type="text" value={injectSource} onChange={e => setInjectSource(e.target.value)} placeholder="https://… или пусто" />
              </div>
            </form>
      </SidePanel>
    </div>
  );
};

export default MuninnExplorer;
