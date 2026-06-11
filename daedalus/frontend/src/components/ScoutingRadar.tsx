import { useEffect, useState } from 'react';
import { Radar, Instagram, Twitter, MessageCircle, Flame, X, Rocket, RefreshCw } from 'lucide-react';
import './ScoutingRadar.css';

export interface MissionPrefill {
  target_url: string;
  title: string;
  narrative_goal: string;
}

interface ScoutingRadarProps {
  token: string;
  onConverted: (prefill: MissionPrefill) => void;
}

interface ScoutedTarget {
  id: number;
  platform: string;
  url: string;
  author_name: string | null;
  content_summary: string | null;
  velocity_score: number;
  engagement: number;
  posted_at: number | null;
  status: string;
}

const platformIcon = (platform: string) => {
  const p = platform.toLowerCase();
  if (p === 'instagram') return <Instagram size={16} />;
  if (p === 'x' || p === 'twitter') return <Twitter size={16} />;
  if (p === 'threads') return <MessageCircle size={16} />;
  return <Radar size={16} />;
};

// Heat map: yellow (cool-viral) → red (white-hot) based on velocity.
const heatColor = (score: number): string => {
  const t = Math.max(0, Math.min(1, score / 2000)); // saturate at 2000/hr
  const hue = 50 - 50 * t; // 50° (yellow) → 0° (red)
  return `hsl(${hue}, 95%, 52%)`;
};

const relativeTime = (epoch: number | null): string => {
  if (!epoch) return 'unknown time';
  const secs = Math.max(0, Math.floor(Date.now() / 1000) - epoch);
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
};

const ScoutingRadar: React.FC<ScoutingRadarProps> = ({ token, onConverted }) => {
  const [targets, setTargets] = useState<ScoutedTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

  const showToast = (text: string, type: 'success' | 'error') => {
    setToast({ text, type });
    setTimeout(() => setToast(null), 4000);
  };

  useEffect(() => {
    fetchRadar();
    const interval = setInterval(fetchRadar, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchRadar = async () => {
    try {
      const res = await fetch('/api/v1/scouting/radar', { headers });
      if (res.ok) setTargets(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleDismiss = async (id: number) => {
    // Optimistic removal.
    setTargets(prev => prev.filter(t => t.id !== id));
    try {
      await fetch(`/api/v1/scouting/${id}/dismiss`, { method: 'POST', headers });
    } catch (e) {
      console.error(e);
      fetchRadar();
    }
  };

  const handleConvert = async (t: ScoutedTarget) => {
    try {
      const res = await fetch(`/api/v1/scouting/${t.id}/convert`, { method: 'POST', headers });
      const data = await res.json();
      if (res.ok) {
        setTargets(prev => prev.filter(x => x.id !== t.id));
        showToast(`Mission #${data.mission_id} drafted. Redirecting to Mission Deck…`, 'success');
        onConverted({
          target_url: data.target_url,
          title: data.title,
          narrative_goal: t.content_summary || '',
        });
      } else {
        showToast(`Convert failed: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    }
  };

  return (
    <div className="scouting-radar view-container">
      {toast && <div className={`sr-toast ${toast.type}`}>{toast.text}</div>}

      <div className="header-row">
        <div>
          <h1><Radar size={22} style={{ verticalAlign: '-4px' }} /> Scouting Radar</h1>
          <p className="subtitle">Authenticated viral discoveries from HUGINN, ranked by engagement velocity.</p>
        </div>
        <button className="btn-primary sr-refresh" onClick={fetchRadar}><RefreshCw size={14} /> Refresh</button>
      </div>

      {loading ? (
        <p>Scanning radar…</p>
      ) : targets.length === 0 ? (
        <div className="sr-empty">
          <Radar size={48} />
          <p>No viral targets detected yet.</p>
          <span>Supply social cookies to an account and HUGINN will surface hot posts here.</span>
        </div>
      ) : (
        <div className="sr-grid">
          {targets.map(t => (
            <div key={t.id} className="sr-card" style={{ borderTopColor: heatColor(t.velocity_score) }}>
              <div className="sr-card-head">
                <span className="sr-platform">{platformIcon(t.platform)} {t.platform}</span>
                <span className="sr-time">{relativeTime(t.posted_at)}</span>
              </div>

              <div className="sr-author">@{t.author_name || 'unknown'}</div>
              <p className="sr-snippet">{t.content_summary || '(no caption)'}</p>

              <div className="sr-velocity">
                <div className="sr-velocity-badge" style={{ background: heatColor(t.velocity_score) }}>
                  <Flame size={14} /> {Math.round(t.velocity_score).toLocaleString()}/h
                </div>
                <span className="sr-engagement">{t.engagement.toLocaleString()} engagements</span>
              </div>

              <a className="sr-link" href={t.url} target="_blank" rel="noreferrer">{t.url}</a>

              <div className="sr-actions">
                <button className="sr-dismiss" onClick={() => handleDismiss(t.id)}>
                  <X size={14} /> Dismiss
                </button>
                <button className="sr-convert" onClick={() => handleConvert(t)}>
                  <Rocket size={14} /> Convert to Mission
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default ScoutingRadar;
