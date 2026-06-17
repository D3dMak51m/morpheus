import { useEffect, useState } from 'react';
import SystemDiagnostics from './SystemDiagnostics';
import { Activity, Server, Database, Radar, ShieldCheck, ShieldAlert, RefreshCw } from 'lucide-react';
import './Dashboard.css';

interface DashboardProps {
  token: string;
}

interface Overlord {
  readiness: 'GO' | 'NO-GO';
  blockers: string[];
  missions: { active: number; total: number };
  fleet: { online: number; total: number; recovering: number };
  accounts: { active: number };
  database: {
    captured_events: number;
    scouted_pending: number;
    activity_logs: number;
    retention: { last_run_at: string | null; captured_pruned: number; targets_pruned: number; status: string };
  };
  huginn: { scouted_last_hour: number; top_velocity: number };
  timestamp: string;
}

const BLOCKER_LABELS: Record<string, string> = {
  database: 'База данных недоступна',
  fleet_online: 'Нет эмуляторов онлайн',
  accounts_active: 'Нет активных аккаунтов',
  no_recovering_devices: 'Устройства восстанавливаются',
};

function SwarmOverlord({ token }: { token: string }) {
  const [data, setData] = useState<Overlord | null>(null);
  const [error, setError] = useState('');

  const fetchOverlord = async () => {
    try {
      const res = await fetch('/api/v1/analytics/overlord', { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setError('');
    } catch (e: any) {
      setError(e.message || 'Не удалось загрузить');
    }
  };

  useEffect(() => {
    fetchOverlord();
    const interval = setInterval(fetchOverlord, 8000);
    return () => clearInterval(interval);
  }, []);

  if (error && !data) {
    return <div className="overlord-widget error-banner">Оверлорд роя недоступен: {error}</div>;
  }
  if (!data) {
    return <div className="overlord-widget">Загрузка состояния роя…</div>;
  }

  const isGo = data.readiness === 'GO';
  const fleetPct = data.fleet.total > 0 ? Math.round((data.fleet.online / data.fleet.total) * 100) : 0;
  const lastPrune = data.database.retention.last_run_at
    ? new Date(data.database.retention.last_run_at).toLocaleString('ru-RU')
    : 'никогда';

  return (
    <div className={`overlord-widget ${isGo ? 'go' : 'nogo'}`}>
      <div className="overlord-header">
        <div className="overlord-title">
          <Activity size={18} /> Состояние роя
        </div>
        <div className={`overlord-readiness ${isGo ? 'go' : 'nogo'}`}>
          {isGo ? <ShieldCheck size={20} /> : <ShieldAlert size={20} />}
          {isGo ? 'ГОТОВ' : 'НЕ ГОТОВ'}
        </div>
      </div>

      {!isGo && data.blockers.length > 0 && (
        <div className="overlord-blockers">
          Блокеры: {data.blockers.map(b => BLOCKER_LABELS[b] || b).join(' · ')}
        </div>
      )}

      <div className="overlord-grid">
        <div className="overlord-stat">
          <div className="overlord-stat-icon"><Server size={18} /></div>
          <div className="overlord-stat-value">{data.fleet.online}/{data.fleet.total}</div>
          <div className="overlord-stat-label">Эмуляторы онлайн ({fleetPct}%)</div>
          {data.fleet.recovering > 0 && <div className="overlord-stat-sub recovering">{data.fleet.recovering} восстанавливается</div>}
        </div>

        <div className="overlord-stat">
          <div className="overlord-stat-icon"><Activity size={18} /></div>
          <div className="overlord-stat-value">{data.missions.active}</div>
          <div className="overlord-stat-label">Активные миссии</div>
          <div className="overlord-stat-sub">всего {data.missions.total}</div>
        </div>

        <div className="overlord-stat">
          <div className="overlord-stat-icon"><Radar size={18} /></div>
          <div className="overlord-stat-value">{data.huginn.scouted_last_hour}</div>
          <div className="overlord-stat-label">Найдено / за час</div>
          <div className="overlord-stat-sub">пик {Math.round(data.huginn.top_velocity).toLocaleString('ru-RU')}/ч</div>
        </div>

        <div className="overlord-stat">
          <div className="overlord-stat-icon"><Database size={18} /></div>
          <div className="overlord-stat-value">{data.database.captured_events.toLocaleString('ru-RU')}</div>
          <div className="overlord-stat-label">Перехвачено событий</div>
          <div className="overlord-stat-sub">очищено {lastPrune}</div>
        </div>

        <div className="overlord-stat">
          <div className="overlord-stat-icon"><ShieldCheck size={18} /></div>
          <div className="overlord-stat-value">{data.accounts.active}</div>
          <div className="overlord-stat-label">Активные аккаунты</div>
        </div>

        <div className="overlord-stat">
          <div className="overlord-stat-icon"><RefreshCw size={18} /></div>
          <div className="overlord-stat-value">{data.database.scouted_pending}</div>
          <div className="overlord-stat-label">В очереди радара</div>
          <div className="overlord-stat-sub">{data.database.activity_logs.toLocaleString('ru-RU')} действий записано</div>
        </div>
      </div>
    </div>
  );
}

export default function Dashboard({ token }: DashboardProps) {
  const [activeTab, setActiveTab] = useState<'overview' | 'diagnostics'>('overview');

  return (
    <div className="view-container dashboard-container">
      <div className="dashboard-header">
        <h1>Дашборд системы</h1>
        <div className="tabs dashboard-tabs">
          <button
            className={`tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            Обзор
          </button>
          <button
            className={`tab-btn ${activeTab === 'diagnostics' ? 'active' : ''}`}
            onClick={() => setActiveTab('diagnostics')}
          >
            Диагностика
          </button>
        </div>
      </div>

      <div className="dashboard-content">
        {activeTab === 'overview' && (
          <div className="overview-panel">
            <SwarmOverlord token={token} />
            <h2 style={{ marginTop: '24px' }}>Добро пожаловать в Daedalus — пульт управления Morpheus.</h2>
            <p className="text-muted">Выберите раздел в меню слева, чтобы начать.</p>
          </div>
        )}

        {activeTab === 'diagnostics' && (
          <SystemDiagnostics token={token} />
        )}
      </div>
    </div>
  );
}
