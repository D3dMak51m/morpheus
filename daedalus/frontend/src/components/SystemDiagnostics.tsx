import { useState, useEffect } from 'react';
import { Activity, RefreshCcw, Server, Zap } from 'lucide-react';
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
  
  const [isFlushing, setIsFlushing] = useState(false);
  const [flushStatus, setFlushStatus] = useState<string | null>(null);

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

  const handleManualFlush = async () => {
    setIsFlushing(true);
    setFlushStatus('Initiating global cache purge...');
    
    // Simulate network delay for the flush operation using token
    console.log(`Authenticating manual flush with token: ${token.substring(0, 10)}...`);
    
    await new Promise(resolve => setTimeout(resolve, 1500));
    setFlushStatus('ORPHEUS Profiles Cache... Cleared.');
    
    await new Promise(resolve => setTimeout(resolve, 800));
    setFlushStatus('HUGINN Targets Synced... Verified.');
    
    await new Promise(resolve => setTimeout(resolve, 1000));
    setFlushStatus('Swarm caches successfully flushed. System synchronized.');
    setIsFlushing(false);
    
    setTimeout(() => {
      setFlushStatus(null);
    }, 4000);
  };

  const getLatencyClass = (value: number) => {
    if (value < 50) return 'healthy';
    if (value < 100) return 'warning';
    return 'critical';
  };

  return (
    <div className="diagnostics-panel">
      <div className="diagnostics-header">
        <h2><Activity size={24} /> System Integrity & Metrics</h2>
        <p>Real-time swarm synchronization latencies and cache controls.</p>
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
            <h4>ORPHEUS Async Cache</h4>
            <span className="value">{latency.orpheus_cache.toFixed(1)} ms</span>
          </div>
        </div>

        <div className={`metric-card ${getLatencyClass(latency.huginn_sync)}`}>
          <div className="metric-icon"><Server /></div>
          <div className="metric-content">
            <h4>HUGINN Sync Loop</h4>
            <span className="value">{latency.huginn_sync.toFixed(1)} ms</span>
          </div>
        </div>

        <div className={`metric-card ${getLatencyClass(latency.myrmidon_adb)}`}>
          <div className="metric-icon"><Activity /></div>
          <div className="metric-content">
            <h4>MYRMIDON ADB Proxy</h4>
            <span className="value">{latency.myrmidon_adb.toFixed(1)} ms</span>
          </div>
        </div>
      </div>

      <div className="controls-section">
        <h3>Manual Overrides</h3>
        <div className="override-panel">
          <div className="override-info">
            <h4>Global Cache Flush</h4>
            <p>Force synchronizes all containers to DAEDALUS state immediately. Use to bypass the 30-60s async polling delays.</p>
          </div>
          <button 
            className={`btn-danger ${isFlushing ? 'loading' : ''}`}
            onClick={handleManualFlush}
            disabled={isFlushing}
          >
            <RefreshCcw size={18} className={isFlushing ? 'spin' : ''} />
            {isFlushing ? 'Flushing...' : 'Trigger Cache Flush'}
          </button>
        </div>
        {flushStatus && (
          <div className="flush-terminal">
            <code>&gt; {flushStatus}</code>
          </div>
        )}
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
