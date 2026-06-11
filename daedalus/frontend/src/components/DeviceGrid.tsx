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

interface OrchestratedDevice {
  id: string;
  name: string;
  status: string;
  vnc_port: string;
  adb_port: string;
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
  const [assignMap, setAssignMap] = useState<Record<number, string>>({});
  const [loadingStates, setLoadingStates] = useState<Record<string, boolean>>({});
  const [toastMessage, setToastMessage] = useState<{text: string, type: 'success' | 'error'} | null>(null);
  
  const [orchEmulators, setOrchEmulators] = useState<OrchestratedDevice[]>([]);
  const [newEmuName, setNewEmuName] = useState('');
  const [vncUrl, setVncUrl] = useState<string | null>(null);

  const showToast = (text: string, type: 'success' | 'error') => {
    setToastMessage({text, type});
    setTimeout(() => setToastMessage(null), 5000);
  };

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

  const fetchOrchestrator = async () => {
    try {
      const res = await fetch('/api/v1/analytics/orchestrator/list', { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        setOrchEmulators(data.emulators || []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchDevices();
    fetchTelemetry();
    fetchOrchestrator();
    const interval = setInterval(() => {
      fetchTelemetry();
      fetchOrchestrator();
    }, 5000);
    return () => clearInterval(interval);
  }, []);



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

  const handleProvision = async () => {
    if (!newEmuName) return;
    setLoadingStates(prev => ({...prev, 'provision': true}));
    try {
      const res = await fetch(`/api/v1/analytics/orchestrator/create`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({name: newEmuName})
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`Provisioned ${data.name} on ADB ${data.adb_port}`, 'success');
        setNewEmuName('');
        fetchOrchestrator();
        
        // Auto-register to Virtual Device Fleet
        try {
          await fetch(`/api/v1/souls/devices?device_id=localhost:${data.adb_port}`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` }
          });
          fetchDevices();
        } catch(e) {
          console.error("Auto-registration failed", e);
        }

      } else {
        showToast(`Failed to provision: ${data.detail || data.error}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setLoadingStates(prev => ({...prev, 'provision': false}));
    }
  };

  const handleOrchControl = async (name: string, action: string) => {
    setLoadingStates(prev => ({...prev, [`${name}-${action}`]: true}));
    try {
      const res = await fetch(`/api/v1/analytics/orchestrator/${name}${action === 'delete' ? '' : '/'+action}`, {
        method: action === 'delete' ? 'DELETE' : 'POST',
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        showToast(`Success [${action}]`, 'success');
        fetchOrchestrator();
      } else {
        const data = await res.json();
        showToast(`Error: ${data.detail || data.error}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error: ${e.message}`, 'error');
    } finally {
      setLoadingStates(prev => ({...prev, [`${name}-${action}`]: false}));
    }
  };

  const handleHardwareControl = async (deviceId: string, action: string, payload: any) => {
    const actionKey = `${deviceId}-${action}`;
    setLoadingStates(prev => ({...prev, [actionKey]: true}));
    try {
      const res = await fetch(`/api/v1/analytics/devices/${deviceId}/${action}`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`Success [${action}]: ${data.status || 'OK'}`, 'success');
      } else {
        showToast(`Error [${action}]: ${data.detail || data.error || 'Failed'}`, 'error');
      }
    } catch (e: any) {
      showToast(`Error [${action}]: ${e.message}`, 'error');
    } finally {
      setLoadingStates(prev => ({...prev, [actionKey]: false}));
    }
  };

  const handleDeleteDevice = async (id: number) => {
    try {
      await fetch(`/api/v1/souls/devices/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` }
      });
      fetchDevices();
    } catch (e) {
      console.error(e);
    }
  };

  const handleCreateExplicit = async (deviceId: string) => {
    try {
      await fetch(`/api/v1/souls/devices?device_id=${deviceId}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      });
      fetchDevices();
    } catch (e) {
      console.error(e);
    }
  };

  // Merge logic
  const mergedDevices: any[] = [];
  devices.forEach(d => {
    const orch = orchEmulators.find(e => `localhost:${e.adb_port}` === d.device_id || e.name === d.device_id);
    mergedDevices.push({
      isVirtual: true,
      isOrchestrated: !!orch,
      virtualId: d.id,
      deviceId: d.device_id,
      assignedAgentId: d.assigned_agent_id,
      orchName: orch?.name,
      vncPort: orch?.vnc_port,
      adbPort: orch?.adb_port,
      status: orch?.status
    });
  });

  orchEmulators.forEach(e => {
    const exists = mergedDevices.find(m => m.orchName === e.name);
    if (!exists) {
      mergedDevices.push({
        isVirtual: false,
        isOrchestrated: true,
        deviceId: `localhost:${e.adb_port}`,
        orchName: e.name,
        vncPort: e.vnc_port,
        adbPort: e.adb_port,
        status: e.status
      });
    }
  });

  return (
    <div className="view-container device-registry relative">
      {toastMessage && (
        <div className={`absolute top-4 right-4 p-4 rounded text-white z-50 transition-opacity ${toastMessage.type === 'success' ? 'bg-green-600' : 'bg-red-600'}`}>
          {toastMessage.text}
        </div>
      )}
      
      {vncUrl && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80" style={{position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 9999, background: 'rgba(0,0,0,0.8)', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          <div className="bg-gray-900 rounded-lg overflow-hidden border border-white/20 shadow-2xl relative" style={{background: '#111', border: '1px solid rgba(255,255,255,0.2)', borderRadius: '8px', width: '80%', height: '80%', display: 'flex', flexDirection: 'column'}}>
            <div className="p-3 border-b border-white/10 flex justify-between items-center" style={{padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
              <h3 className="text-white font-semibold m-0">Live VNC Stream</h3>
              <button onClick={() => setVncUrl(null)} className="text-white hover:text-red-400" style={{background: 'transparent', border: 'none', color: 'white', cursor: 'pointer', fontSize: '16px'}}>Close</button>
            </div>
            <iframe src={vncUrl} className="w-full flex-grow border-none" style={{width: '100%', flexGrow: 1, border: 'none', height: '100%'}}></iframe>
          </div>
        </div>
      )}

      <div className="header-row">
        <div>
          <h1>Unified Device Manager</h1>
          <p className="subtitle">Manage device allocations, live interaction, and orchestration</p>
        </div>
        <div className="create-device-form">
          <input 
            type="text" 
            placeholder="New Emulator Name" 
            value={newEmuName} 
            onChange={e => setNewEmuName(e.target.value)}
          />
          <button onClick={handleProvision} disabled={loadingStates['provision']} className="btn-primary">
            {loadingStates['provision'] ? 'Provisioning (Wait...)' : 'Provision Emulator'}
          </button>
        </div>
      </div>

      <div className="device-grid">
        {mergedDevices.map((d, idx) => {
          const tel = telemetry[d.deviceId];
          const isOnline = tel?.state === 'online';
          const memUsed = tel ? (tel.mem_total_mb - tel.mem_free_mb) : 0;

          return (
            <div key={`${d.deviceId}-${idx}`} className="device-card">
              <div className="device-header" style={{display: 'flex', justifyContent: 'space-between'}}>
                <h3><Smartphone size={16}/> {d.orchName || d.deviceId}</h3>
                <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
                  {d.isVirtual && <button className="btn-icon text-danger" onClick={() => handleDeleteDevice(d.virtualId)} title="Remove from Daedalus"><Unlink size={14}/></button>}
                  <div className={`status-dot ${isOnline || (d.isOrchestrated && d.status?.includes('Up')) ? 'online' : 'offline'}`} />
                </div>
              </div>

              {d.isOrchestrated && (
                <div className="text-xs mt-2 text-muted" style={{display: 'flex', gap: '10px'}}>
                  <span><strong>Docker:</strong> {d.status}</span>
                  <span><strong>ADB:</strong> {d.adbPort}</span>
                  <span><strong>VNC:</strong> {d.vncPort}</span>
                </div>
              )}

              {d.isVirtual ? (
                <div className="device-assignment mt-3">
                  {d.assignedAgentId ? (
                    <div className="flex-between">
                      <span className="agent-tag"><Shield size={12}/> {d.assignedAgentId}</span>
                      <button className="btn-icon text-danger" onClick={() => handleAssign(d.virtualId, null)}><Unlink size={14}/></button>
                    </div>
                  ) : (
                    <div className="assign-inputs">
                      <input 
                        type="text" 
                        placeholder="Assign agent..." 
                        value={assignMap[d.virtualId] || ''}
                        onChange={e => setAssignMap({...assignMap, [d.virtualId]: e.target.value})}
                      />
                      <button className="btn-icon text-success" onClick={() => handleAssign(d.virtualId, assignMap[d.virtualId])}><Link size={14}/></button>
                    </div>
                  )}
                </div>
              ) : (
                <div className="mt-3">
                  <button className="btn-secondary text-xs w-full" onClick={() => handleCreateExplicit(d.deviceId)}>Register into Daedalus Fleet</button>
                </div>
              )}

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
                  <div className="offline-state text-muted text-xs mt-2">
                    Telemetry data offline or booting
                  </div>
                )}
              </div>

              <div className="hardware-control-deck mt-4 pt-4" style={{borderTop: '1px solid rgba(255,255,255,0.1)'}}>
                <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px'}}>
                  {d.isVirtual && (
                    <>
                      <button className="btn-secondary text-xs" onClick={() => handleHardwareControl(d.deviceId, 'reboot', {})}>
                        Reboot
                      </button>
                      <button className="btn-warning text-xs" onClick={() => handleHardwareControl(d.deviceId, 'clear-cache', {package: 'com.android.chrome'})}>
                        Clear Chrome
                      </button>
                    </>
                  )}
                  {d.isOrchestrated && (
                    <>
                      <button className="btn-primary text-xs" onClick={() => setVncUrl(`http://${window.location.hostname}:${d.vncPort}`)}>
                        Live View
                      </button>
                      <button className="btn-danger text-xs" onClick={() => handleOrchControl(d.orchName, 'delete')}>
                        Destroy
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default DeviceGrid;
