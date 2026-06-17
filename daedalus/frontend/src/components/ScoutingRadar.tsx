import { useEffect, useState } from 'react';
import { Radar, Instagram, Twitter, MessageCircle, Flame, X, Rocket, RefreshCw } from 'lucide-react';
import { DataTable, Column } from './DataTable';
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
  if (!epoch) return 'неизвестно';
  const secs = Math.max(0, Math.floor(Date.now() / 1000) - epoch);
  if (secs < 3600) return `${Math.floor(secs / 60)} мин назад`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} ч назад`;
  return `${Math.floor(secs / 86400)} дн назад`;
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
        showToast(`Миссия #${data.mission_id} создана. Переход в Mission Deck…`, 'success');
        onConverted({
          target_url: data.target_url,
          title: data.title,
          narrative_goal: t.content_summary || '',
        });
      } else {
        showToast(`Не удалось создать миссию: ${data.detail || res.status}`, 'error');
      }
    } catch (e: any) {
      showToast(`Ошибка: ${e.message}`, 'error');
    }
  };

  const columns: Column<ScoutedTarget>[] = [
    { key: 'platform', header: 'Платформа', width: '120px',
      sortValue: t => t.platform,
      render: t => <span className="sr-platform">{platformIcon(t.platform)} {t.platform}</span> },
    { key: 'author_name', header: 'Автор', width: '150px',
      sortValue: t => (t.author_name || '').toLowerCase(),
      render: t => <span className="sr-author-cell">@{t.author_name || 'unknown'}</span> },
    { key: 'content_summary', header: 'Содержание', sortable: false,
      render: t => <span className="sr-snippet-cell">{t.content_summary || '(без текста)'}</span> },
    { key: 'velocity_score', header: 'Скорость', width: '130px', align: 'right',
      sortValue: t => t.velocity_score,
      render: t => (
        <span className="sr-velocity-badge" style={{ background: heatColor(t.velocity_score) }}>
          <Flame size={13} /> {Math.round(t.velocity_score).toLocaleString('ru-RU')}/ч
        </span>
      ) },
    { key: 'engagement', header: 'Вовлечённость', width: '130px', align: 'right',
      sortValue: t => t.engagement,
      render: t => <span className="sr-engagement-cell">{t.engagement.toLocaleString('ru-RU')}</span> },
    { key: 'posted_at', header: 'Когда', width: '110px',
      sortValue: t => t.posted_at || 0,
      render: t => <span className="sr-time">{relativeTime(t.posted_at)}</span> },
    { key: 'link', header: '', sortable: false, width: '60px',
      render: t => <a className="sr-link" href={t.url} target="_blank" rel="noreferrer">пост ↗</a> },
    { key: 'actions', header: 'Действия', sortable: false, width: '210px',
      render: t => (
        <div className="sr-actions">
          <button className="sr-dismiss" onClick={() => handleDismiss(t.id)}>
            <X size={13} /> Скрыть
          </button>
          <button className="sr-convert" onClick={() => handleConvert(t)}>
            <Rocket size={13} /> В миссию
          </button>
        </div>
      ) },
  ];

  return (
    <div className="scouting-radar view-container">
      {toast && <div className={`sr-toast ${toast.type}`}>{toast.text}</div>}

      <div className="header-row">
        <div>
          <h1><Radar size={22} style={{ verticalAlign: '-4px' }} /> Радар разведки</h1>
          <p className="subtitle">Вирусные находки из HUGINN, ранжированные по скорости набора вовлечённости. Сортируйте по «Скорости», ищите по автору или тексту, превращайте горячий пост в миссию.</p>
        </div>
        <button className="btn-primary sr-refresh" onClick={fetchRadar}><RefreshCw size={14} /> Обновить</button>
      </div>

      <DataTable
        columns={columns}
        rows={targets}
        rowKey={t => t.id}
        loading={loading}
        searchText={t => `${t.platform} ${t.author_name || ''} ${t.content_summary || ''} ${t.url}`}
        searchPlaceholder="🔍 Поиск по автору, тексту или платформе…"
        emptyText="Вирусных целей пока нет. Привяжите соц-куки к аккаунту — HUGINN начнёт находить горячие посты."
        pageSize={25}
      />
    </div>
  );
};

export default ScoutingRadar;
