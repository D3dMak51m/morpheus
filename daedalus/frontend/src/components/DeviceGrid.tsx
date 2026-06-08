import { useEffect, useState, useRef } from 'react';
import './DeviceGrid.css';

interface Device {
  device_id: string;
  state: string;
  model: string;
  battery_level: number;
  cpu_load_1m: number;
  mem_total_mb: number;
  mem_free_mb: number;
  current_proxy: string | null;
}

interface DeviceGridProps {
  token: string;
}

const TelemetryCanvas: React.FC<{ value: number; max: number; color: string }> = ({ value, max, color }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    ctx.fillStyle = 'rgba(255, 255, 255, 0.1)';
    ctx.fillRect(0, 0, width, height);

    const pct = Math.max(0, Math.min(1, value / max));
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, width * pct, height);
  }, [value, max, color]);

  return <canvas ref={canvasRef} width={200} height={12} className="telemetry-canvas" />;
};

const DeviceGrid: React.FC<DeviceGridProps> = ({ token }) => {
  const [devices, setDevices] = useState<Device[]>([]);
  const [error, setError] = useState('');

  const fetchDevices = async () => {
    try {
      const res = await fetch('/api/v1/analytics/devices', {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setDevices(data.devices || []);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch devices');
    }
  };

  useEffect(() => {
    fetchDevices();
    const interval = setInterval(fetchDevices, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="view-container">
      <div className="header-row">
        <h1>Device Map</h1>
        <p className="subtitle">Real-time ADB Telemetry</p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="device-grid">
        {devices.map(d => {
          const isOnline = d.state === 'online';
          const memUsed = d.mem_total_mb ? d.mem_total_mb - d.mem_free_mb : 0;

          return (
            <div key={d.device_id} className="device-card">
              <div className="device-header">
                <h3>{d.model || 'Unknown Device'}</h3>
                <div className={`status-dot ${isOnline ? 'online' : 'offline'}`} />
              </div>
              <p className="device-id font-mono">{d.device_id}</p>

              <div className="telemetry-section">
                <div className="telemetry-row">
                  <label>CPU Load</label>
                  <span>{d.cpu_load_1m?.toFixed(2) || '0.00'}</span>
                </div>
                <TelemetryCanvas value={d.cpu_load_1m || 0} max={8} color={d.cpu_load_1m > 4 ? '#ef4444' : '#3b82f6'} />

                <div className="telemetry-row">
                  <label>Memory</label>
                  <span>{memUsed} / {d.mem_total_mb} MB</span>
                </div>
                <TelemetryCanvas value={memUsed} max={d.mem_total_mb || 100} color="#8b5cf6" />

                <div className="telemetry-row mt-2">
                  <label>Proxy</label>
                  <span className="font-mono text-sm">{d.current_proxy || 'Direct'}</span>
                </div>
              </div>
            </div>
          );
        })}
        {devices.length === 0 && !error && (
          <div className="empty-state">No active devices monitored by ADB Supervisor.</div>
        )}
      </div>
    </div>
  );
};

export default DeviceGrid;
