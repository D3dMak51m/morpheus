import { useState, useEffect } from 'react';
import { Activity, Server, Zap } from 'lucide-react';
import './SystemDiagnostics.css';

interface DiagnosticsProps {
  token: string;
}

interface LatencyMetrics {
  daedalus_db: number;
  orpheus_cache: number;
  huginn_sync: number;
  myrmidon_adb: number;
}

export default function SystemDiagnostics({ token }: DiagnosticsProps) {
  const [latency, setLatency] = useState<LatencyMetrics>({
    daedalus_db: 0,
    orpheus_cache: 0,
    huginn_sync: 0,
    myrmidon_adb: 0,
  });

  // Poll real latency telemetry
  useEffect(() => {
    const fetchLatency = async () => {
      try {
        const response = await fetch('/api/v1/analytics/latency', {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        });
        if (response.ok) {
          const data = await response.json();
          setLatency(data);
        }
      } catch (error) {
        console.error('Failed to fetch telemetry', error);
      }
    };

    fetchLatency(); // Initial fetch
    const interval = setInterval(fetchLatency, 2000);
    return () => clearInterval(interval);
  }, [token]);

  const getLatencyClass = (value: number) => {
    if (value < 50) return 'healthy';
    if (value < 100) return 'warning';
    return 'critical';
  };

  return (
    <div className="diagnostics-panel">
      <div className="diagnostics-header">
        <h2><Activity size={24} /> Целостность системы и метрики</h2>
        <p>Задержки синхронизации сервисов роя в реальном времени.</p>
      </div>

      <div className="metrics-grid">
        <div className={`metric-card ${getLatencyClass(latency.daedalus_db)}`}>
          <div className="metric-icon"><DatabaseIcon /></div>
          <div className="metric-content">
            <h4>DAEDALUS PostgreSQL</h4>
            <span className="value">{latency.daedalus_db.toFixed(1)} ms</span>
          </div>
        </div>

        <div className={`metric-card ${getLatencyClass(latency.orpheus_cache)}`}>
          <div className="metric-icon"><Zap /></div>
          <div className="metric-content">
            <h4>ORPHEUS · кэш</h4>
            <span className="value">{latency.orpheus_cache.toFixed(1)} ms</span>
          </div>
        </div>

        <div className={`metric-card ${getLatencyClass(latency.huginn_sync)}`}>
          <div className="metric-icon"><Server /></div>
          <div className="metric-content">
            <h4>HUGINN · цикл синхр.</h4>
            <span className="value">{latency.huginn_sync.toFixed(1)} ms</span>
          </div>
        </div>

        <div className={`metric-card ${getLatencyClass(latency.myrmidon_adb)}`}>
          <div className="metric-icon"><Activity /></div>
          <div className="metric-content">
            <h4>MYRMIDON · ADB-прокси</h4>
            <span className="value">{latency.myrmidon_adb.toFixed(1)} ms</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function DatabaseIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
    </svg>
  );
}
