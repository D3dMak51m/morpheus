import { useEffect, useState, useRef } from 'react';
import { Shield, Smartphone, HardDrive, Cpu, Link, Unlink, Trash2, Plus } from 'lucide-react';
import { Box, Group, Stack, Title, Text, TextInput, Button, SimpleGrid, Paper, Badge, Modal, ActionIcon, Tooltip, Notification } from '@mantine/core';
import { EntityPicker } from '../ui/EntityPicker';

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
    <Box p="lg">
      {toastMessage && (
        <Notification color={toastMessage.type === 'success' ? 'teal' : 'red'} onClose={() => setToastMessage(null)}
          style={{ position: 'fixed', top: 16, right: 16, zIndex: 1000 }}>{toastMessage.text}</Notification>
      )}

      <Modal opened={!!vncUrl} onClose={() => setVncUrl(null)} title="Прямой VNC-поток" size="80%" centered withinPortal={false}>
        {vncUrl && <iframe src={vncUrl} style={{ width: '100%', height: '70vh', border: 'none', borderRadius: 8 }} />}
      </Modal>

      <Group justify="space-between" mb="md" align="flex-start">
        <div>
          <Title order={2}><HardDrive size={22} style={{ verticalAlign: -4 }} /> Устройства</Title>
          <Text size="sm" c="dimmed" maw={620}>Управление виртуальными устройствами роя, прямое взаимодействие и оркестрация эмуляторов. (Мобильный стек вне scope — телеметрия может быть пустой.)</Text>
        </div>
        <Group gap="xs">
          <TextInput placeholder="Имя нового эмулятора" value={newEmuName} onChange={e => setNewEmuName(e.currentTarget.value)} />
          <Button leftSection={<Plus size={15} />} loading={loadingStates['provision']} onClick={handleProvision}>Создать эмулятор</Button>
        </Group>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {mergedDevices.map((d, idx) => {
          const tel = telemetry[d.deviceId];
          const isOnline = tel?.state === 'online' || (d.isOrchestrated && d.status?.includes('Up'));
          const memUsed = tel ? (tel.mem_total_mb - tel.mem_free_mb) : 0;
          return (
            <Paper key={`${d.deviceId}-${idx}`} withBorder radius="md" p="md">
              <Group justify="space-between" mb="xs">
                <Group gap={6}><Smartphone size={16} /><Text fw={600} size="sm">{d.orchName || d.deviceId}</Text></Group>
                <Group gap="xs">
                  {d.isVirtual && <Tooltip label="Убрать из Daedalus"><ActionIcon variant="subtle" color="red" onClick={() => handleDeleteDevice(d.virtualId)}><Trash2 size={15} /></ActionIcon></Tooltip>}
                  <Box style={{ width: 10, height: 10, borderRadius: 999, background: isOnline ? '#34c98b' : '#64748b' }} />
                </Group>
              </Group>

              {d.isOrchestrated && (
                <Group gap="md" mb="xs"><Text size="xs" c="dimmed">Docker: {d.status}</Text><Text size="xs" c="dimmed">ADB: {d.adbPort}</Text><Text size="xs" c="dimmed">VNC: {d.vncPort}</Text></Group>
              )}

              {d.isVirtual ? (
                d.assignedAgentId ? (
                  <Group justify="space-between" mb="sm">
                    <Badge variant="light" color="indigo" leftSection={<Shield size={11} />}>{d.assignedAgentId}</Badge>
                    <Button size="xs" variant="subtle" color="red" leftSection={<Unlink size={13} />} onClick={() => handleAssign(d.virtualId, null)}>Отвязать</Button>
                  </Group>
                ) : (
                  <Button fullWidth size="xs" variant="light" mb="sm" leftSection={<Link size={13} />} onClick={() => setPickerFor(d.virtualId)}>Привязать агента</Button>
                )
              ) : (
                <Button fullWidth size="xs" variant="light" mb="sm" onClick={() => handleCreateExplicit(d.deviceId)}>Добавить в реестр Daedalus</Button>
              )}

              {tel ? (
                <Stack gap={4} mb="sm">
                  <Group justify="space-between"><Text size="xs" c="dimmed"><Cpu size={11} style={{ verticalAlign: -1 }} /> Нагрузка</Text><Text size="xs">{tel.cpu_load_1m?.toFixed(2) || '0.00'}</Text></Group>
                  <TelemetryCanvas value={tel.cpu_load_1m || 0} max={8} color={tel.cpu_load_1m > 4 ? '#ef4444' : '#3b82f6'} />
                  <Group justify="space-between"><Text size="xs" c="dimmed"><HardDrive size={11} style={{ verticalAlign: -1 }} /> ОЗУ</Text><Text size="xs">{memUsed} / {tel.mem_total_mb} MB</Text></Group>
                  <TelemetryCanvas value={memUsed} max={tel.mem_total_mb || 100} color="#8b5cf6" />
                </Stack>
              ) : <Text size="xs" c="dimmed" mb="sm">Телеметрия недоступна или устройство загружается</Text>}

              <SimpleGrid cols={2} spacing="xs" style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: 10 }}>
                {d.isVirtual && <>
                  <Button size="xs" variant="default" onClick={() => handleHardwareControl(d.deviceId, 'reboot', {})}>Перезагрузка</Button>
                  <Button size="xs" variant="light" color="yellow" onClick={() => handleHardwareControl(d.deviceId, 'clear-cache', { package: 'com.android.chrome' })}>Очистить Chrome</Button>
                </>}
                {d.isOrchestrated && <>
                  <Button size="xs" onClick={() => setVncUrl(`http://${window.location.hostname}:${d.vncPort}`)}>Экран (VNC)</Button>
                  <Button size="xs" color="red" variant="light" onClick={() => handleOrchControl(d.orchName, 'delete')}>Уничтожить</Button>
                </>}
              </SimpleGrid>
            </Paper>
          );
        })}
      </SimpleGrid>

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
    </Box>
  );
};

export default DeviceGrid;
