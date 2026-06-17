import { useEffect, useState, useRef } from 'react';
import { Shield, Smartphone, HardDrive, Cpu, Link, Unlink } from 'lucide-react';
import { Badge, Text, Stack } from '@mantine/core';
import { EntityPicker } from '../ui/EntityPicker';
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
  const [souls, setSouls] = useState<{ agent_id: string; full_name: string; codename: string; caste: string; status: string }[]>([]);
  const [pickerFor, setPickerFor] = useState<number | null>(null);
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

  const fetchSouls = async () => {
    try {
      const res = await fetch('/api/v1/souls/profiles', { headers: { Authorization: `Bearer ${token}` } });
      if (res.ok) setSouls(await res.json());
    } catch (e) { console.error(e); }
  };

  useEffect(() => {
    fetchDevices();
    fetchTelemetry();
    fetchOrchestrator();
    fetchSouls();
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
        showToast(`Создан ${data.name} (ADB ${data.adb_port})`, 'success');
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
        showToast(`Не удалось создать: ${data.detail || data.error}`, 'error');
      }
    } catch (e: any) {
      showToast(`Ошибка: ${e.message}`, 'error');
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
        showToast(`Готово [${action}]`, 'success');
        fetchOrchestrator();
      } else {
        const data = await res.json();
        showToast(`Ошибка: ${data.detail || data.error}`, 'error');
      }
    } catch (e: any) {
      showToast(`Ошибка: ${e.message}`, 'error');
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
        showToast(`Готово [${action}]: ${data.status || 'OK'}`, 'success');
      } else {
        showToast(`Ошибка [${action}]: ${data.detail || data.error || 'сбой'}`, 'error');
      }
    } catch (e: any) {
      showToast(`Ошибка [${action}]: ${e.message}`, 'error');
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
        <div style={{
          position: 'fixed', top: 16, right: 16, padding: '12px 16px', borderRadius: 8,
          color: '#fff', zIndex: 9999, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
          background: toastMessage.type === 'success' ? '#16a34a' : '#dc2626',
        }}>
          {toastMessage.text}
        </div>
      )}

      {vncUrl && (
        <div style={{position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, zIndex: 9999, background: 'rgba(0,0,0,0.8)', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
          <div style={{background: '#111', border: '1px solid rgba(255,255,255,0.2)', borderRadius: '8px', width: '80%', height: '80%', display: 'flex', flexDirection: 'column'}}>
            <div style={{padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
              <h3 style={{margin: 0, color: '#fff', fontWeight: 600}}>Прямой VNC-поток</h3>
              <button onClick={() => setVncUrl(null)} style={{background: 'transparent', border: 'none', color: 'white', cursor: 'pointer', fontSize: '16px'}}>Закрыть</button>
            </div>
            <iframe src={vncUrl} style={{width: '100%', flexGrow: 1, border: 'none', height: '100%'}}></iframe>
          </div>
        </div>
      )}

      <div className="header-row">
        <div>
          <h1>Устройства</h1>
          <p className="subtitle">Управление виртуальными устройствами роя, прямое взаимодействие и оркестрация эмуляторов. (Мобильный стек вне scope — телеметрия может быть пустой.)</p>
        </div>
        <div className="create-device-form">
          <input
            type="text"
            placeholder="Имя нового эмулятора"
            value={newEmuName}
            onChange={e => setNewEmuName(e.target.value)}
          />
          <button onClick={handleProvision} disabled={loadingStates['provision']} className="btn-primary">
            {loadingStates['provision'] ? 'Создание (ждите…)' : 'Создать эмулятор'}
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
                  {d.isVirtual && <button className="btn-icon text-danger" onClick={() => handleDeleteDevice(d.virtualId)} title="Убрать из Daedalus"><Unlink size={14}/></button>}
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
                    <button className="btn-secondary text-xs w-full" onClick={() => setPickerFor(d.virtualId)}>
                      <Link size={13} /> Привязать агента
                    </button>
                  )}
                </div>
              ) : (
                <div className="mt-3">
                  <button className="btn-secondary text-xs w-full" onClick={() => handleCreateExplicit(d.deviceId)}>Добавить в реестр Daedalus</button>
                </div>
              )}

              <div className="telemetry-section mt-4">
                {tel ? (
                  <>
                    <div className="telemetry-row">
                      <label><Cpu size={12}/> Нагрузка</label>
                      <span>{tel.cpu_load_1m?.toFixed(2) || '0.00'}</span>
                    </div>
                    <TelemetryCanvas value={tel.cpu_load_1m || 0} max={8} color={tel.cpu_load_1m > 4 ? '#ef4444' : '#3b82f6'} />
                    <div className="telemetry-row">
                      <label><HardDrive size={12}/> ОЗУ</label>
                      <span>{memUsed} / {tel.mem_total_mb} MB</span>
                    </div>
                    <TelemetryCanvas value={memUsed} max={tel.mem_total_mb || 100} color="#8b5cf6" />
                  </>
                ) : (
                  <div className="offline-state text-muted text-xs mt-2">
                    Телеметрия недоступна или устройство загружается
                  </div>
                )}
              </div>

              <div className="hardware-control-deck mt-4 pt-4" style={{borderTop: '1px solid rgba(255,255,255,0.1)'}}>
                <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px'}}>
                  {d.isVirtual && (
                    <>
                      <button className="btn-secondary text-xs" onClick={() => handleHardwareControl(d.deviceId, 'reboot', {})}>
                        Перезагрузка
                      </button>
                      <button className="btn-warning text-xs" onClick={() => handleHardwareControl(d.deviceId, 'clear-cache', {package: 'com.android.chrome'})}>
                        Очистить Chrome
                      </button>
                    </>
                  )}
                  {d.isOrchestrated && (
                    <>
                      <button className="btn-primary text-xs" onClick={() => setVncUrl(`http://${window.location.hostname}:${d.vncPort}`)}>
                        Экран (VNC)
                      </button>
                      <button className="btn-danger text-xs" onClick={() => handleOrchControl(d.orchName, 'delete')}>
                        Уничтожить
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <EntityPicker
        opened={pickerFor !== null}
        onClose={() => setPickerFor(null)}
        title="Привязать агента к устройству"
        rows={souls}
        rowKey={s => s.agent_id}
        searchText={s => `${s.full_name} ${s.codename} ${s.agent_id} ${s.caste}`}
        emptyText="Нет доступных агентов."
        columns={[
          { key: 'agent', header: 'Агент', minWidth: 220, render: s => <Stack gap={0}><Text fw={600}>{s.full_name || s.codename}</Text><Text size="xs" c="dimmed" ff="monospace">{s.agent_id}</Text></Stack> },
          { key: 'caste', header: 'Каста', minWidth: 90, render: s => <Badge variant="light">{s.caste}</Badge> },
          { key: 'status', header: 'Статус', minWidth: 110, render: s => <Badge size="sm" variant="light">{s.status}</Badge> },
        ]}
        onPick={s => { if (pickerFor !== null) handleAssign(pickerFor, s.agent_id); setPickerFor(null); }}
      />
    </div>
  );
};

export default DeviceGrid;
