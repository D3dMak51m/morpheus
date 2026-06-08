import { useEffect, useState, useRef } from 'react';
import { Shield, Smartphone, HardDrive, Cpu, Link, Unlink } from 'lucide-react';
import './DeviceGrid.css';

interface VirtualDevice {
  id: number;
  device_id: string;
  assigned_agent_id: string | null;
  status: string;
}

interface TelemetryDevice {
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
  const [devices, setDevices] = useState<VirtualDevice[]>([]);
  const [telemetry, setTelemetry] = useState<Record<string, TelemetryDevice>>({});
  const [newDeviceId, setNewDeviceId] = useState('');
  const [assignMap, setAssignMap] = useState<Record<number, string>>({});

  const fetchDevices = async () => {
    try {
      const res = await fetch('/api/v1/souls/devices', { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setDevices(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchTelemetry = async () => {
    try {
      const res = await fetch('/api/v1/analytics/devices', { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        const map: Record<string, TelemetryDevice> = {};
        for (const t of data.devices || []) {
          map[t.device_id] = t;
        }
        setTelemetry(map);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchDevices();
    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleCreate = async () => {
    if (!newDeviceId) return;
    try {
      await fetch(`/api/v1/souls/devices?device_id=${newDeviceId}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      });
      setNewDeviceId('');
      fetchDevices();
    } catch (e) {
      console.error(e);
    }
  };

  const handleAssign = async (id: number, agentId: string | null) => {
    try {
      await fetch(`/api/v1/souls/devices/${id}/assign?agent_id=${agentId || ''}`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}` }
      });
      fetchDevices();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="view-container device-registry">
      <div className="header-row">
        <div>
          <h1>Virtual Device Fleet</h1>
          <p className="subtitle">Manage device allocations and view realtime telemetry</p>
        </div>
        <div className="create-device-form">
          <input 
            type="text" 
            placeholder="emulator-5554..." 
            value={newDeviceId} 
            onChange={e => setNewDeviceId(e.target.value)}
          />
          <button onClick={handleCreate} className="btn-primary">Register Device</button>
        </div>
      </div>

      <div className="device-grid">
        {devices.map(d => {
          const tel = telemetry[d.device_id];
          const isOnline = tel?.state === 'online';
          const memUsed = tel ? (tel.mem_total_mb - tel.mem_free_mb) : 0;

          return (
            <div key={d.id} className="device-card">
              <div className="device-header">
                <h3><Smartphone size={16}/> {d.device_id}</h3>
                <div className={`status-dot ${isOnline ? 'online' : 'offline'}`} />
              </div>

              <div className="device-assignment">
                {d.assigned_agent_id ? (
                  <div className="flex-between">
                    <span className="agent-tag"><Shield size={12}/> {d.assigned_agent_id}</span>
                    <button className="btn-icon text-danger" onClick={() => handleAssign(d.id, null)}><Unlink size={14}/></button>
                  </div>
                ) : (
                  <div className="assign-inputs">
                    <input 
                      type="text" 
                      placeholder="Assign to agent..." 
                      value={assignMap[d.id] || ''}
                      onChange={e => setAssignMap({...assignMap, [d.id]: e.target.value})}
                    />
                    <button className="btn-icon text-success" onClick={() => handleAssign(d.id, assignMap[d.id])}><Link size={14}/></button>
                  </div>
                )}
              </div>

              <div className="telemetry-section mt-4">
                {tel ? (
                  <>
                    <div className="telemetry-row">
                      <label><Cpu size={12}/> Load</label>
                      <span>{tel.cpu_load_1m?.toFixed(2) || '0.00'}</span>
                    </div>
                    <TelemetryCanvas value={tel.cpu_load_1m || 0} max={8} color={tel.cpu_load_1m > 4 ? '#ef4444' : '#3b82f6'} />

                    <div className="telemetry-row">
                      <label><HardDrive size={12}/> RAM</label>
                      <span>{memUsed} / {tel.mem_total_mb} MB</span>
                    </div>
                    <TelemetryCanvas value={memUsed} max={tel.mem_total_mb || 100} color="#8b5cf6" />
                  </>
                ) : (
                  <div className="offline-state text-muted text-sm mt-4 text-center">
                    Device telemetry offline
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default DeviceGrid;
