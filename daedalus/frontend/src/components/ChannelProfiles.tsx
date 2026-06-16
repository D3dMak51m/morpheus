import React, { useEffect, useState, useCallback } from 'react';
import { Compass, RefreshCw, MapPin, Flame } from 'lucide-react';
import './ChannelProfiles.css';

interface Theme { theme: string; count?: number; }
interface ChannelProfile {
  platform: string;
  channel_ref: string;
  title: string | null;
  geo_layers: string[];
  geo_label: string | null;
  topics: string[];
  tags: string[];
  recent_themes: Theme[];
  summary: string | null;
  audience_tone: string | null;
  language: string | null;
  sample_count: number;
  posts_seen: number;
  last_profiled_at: string | null;
  last_themes_at: string | null;
}

interface Props { token: string; }

const LAYER_COLORS: Record<string, string> = {
  global: '#3b82f6', regional: '#8b5cf6', state: '#ec4899', city: '#f59e0b', personal: '#10b981',
};

const ChannelProfiles: React.FC<Props> = ({ token }) => {
  const [profiles, setProfiles] = useState<ChannelProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/v1/channels/profiles', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProfiles(Array.isArray(data.profiles) ? data.profiles : []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить профили каналов');
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);

  const q = query.trim().toLowerCase();
  const shown = q
    ? profiles.filter(p =>
        (p.title || '').toLowerCase().includes(q) ||
        p.channel_ref.toLowerCase().includes(q) ||
        (p.geo_label || '').toLowerCase().includes(q) ||
        (p.topics || []).some(t => t.toLowerCase().includes(q)))
    : profiles;

  return (
    <div className="channel-profiles view-container">
      <div className="header-row">
        <div>
          <h1><Compass size={22} style={{ verticalAlign: '-4px' }} /> Профили каналов</h1>
          <p className="subtitle">Что рой знает про каждый канал: тематика, гео и что в нём обсуждают сейчас. Этот контекст влияет на то, на какие посты бот реагирует и как комментирует.</p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={fetchProfiles}><RefreshCw size={14} /> Обновить</button>
        </div>
      </div>

      <div className="mx-searchbar">
        <input className="mx-search" placeholder="🔍 Поиск по каналу, гео или теме…"
          value={query} onChange={e => setQuery(e.target.value)} />
        <span className="mx-result-count">{shown.length} канал(ов)</span>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="data-grid-container">
        {loading && profiles.length === 0 ? <p>Загрузка…</p> : (
          <table className="data-grid">
            <thead>
              <tr>
                <th>Канал</th>
                <th>Регион</th>
                <th>Тематика</th>
                <th>Сейчас обсуждают</th>
                <th>Чем является</th>
                <th>Профиль обновлён</th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 ? (
                <tr><td colSpan={6} className="empty-state">Профилей пока нет. Они строятся автоматически по целевым каналам активных миссий (раз в сутки, горячие темы — чаще).</td></tr>
              ) : shown.map(p => (
                <tr key={`${p.platform}:${p.channel_ref}`}>
                  <td>
                    <div className="cp-title">{p.title || p.channel_ref}</div>
                    <div className="cp-ref">{p.channel_ref}{p.language ? ` · ${p.language}` : ''}</div>
                  </td>
                  <td>
                    {p.geo_label && <div className="cp-geo"><MapPin size={13} /> {p.geo_label}</div>}
                    <div className="cp-chip-row">
                      {(p.geo_layers || []).map(l => (
                        <span key={l} className="layer-pill" style={{ background: LAYER_COLORS[l] || '#475569' }}>{l}</span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <div className="cp-chip-row">
                      {(p.topics || []).map(t => <span key={t} className="cp-chip cp-chip-topic">{t}</span>)}
                    </div>
                  </td>
                  <td>
                    <div className="cp-chip-row">
                      {(p.recent_themes || []).map(t => (
                        <span key={t.theme} className="cp-chip cp-chip-hot"><Flame size={11} /> {t.theme}{t.count ? ` ·${t.count}` : ''}</span>
                      ))}
                    </div>
                  </td>
                  <td className="cp-summary">{p.summary || '—'}{p.audience_tone ? <div className="cp-tone">{p.audience_tone}</div> : null}</td>
                  <td className="cp-date">
                    {p.last_profiled_at ? new Date(p.last_profiled_at).toLocaleString('ru-RU') : '—'}
                    <div className="cp-subdate">тем: {p.last_themes_at ? new Date(p.last_themes_at).toLocaleTimeString('ru-RU') : '—'} · {p.sample_count} постов</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default ChannelProfiles;
