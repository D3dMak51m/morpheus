import { useEffect, useRef, useState } from 'react';
import {
  Box, Group, Stack, Title, Text, Grid, Paper, Select, SegmentedControl, Textarea, Button, ScrollArea,
} from '@mantine/core';
import { Terminal, Play, Smartphone, Cpu } from 'lucide-react';

interface SandboxConsoleProps { token: string; }
interface AgentOption { agent_id: string; codename: string; full_name: string; }
interface DeviceOption { device_id: string; vnc_port: string | null; status: string | null; }
interface LogLine { ts: string; text: string; kind: 'info' | 'ok' | 'fail'; }

const TARGET_APPS = ['base', 'instagram', 'telegram'];
const LOG_COLOR = { info: '#cbd5e1', ok: '#34c98b', fail: '#ef4444' } as const;

const SandboxConsole = ({ token }: SandboxConsoleProps) => {
  const headers = { Authorization: `Bearer ${token}` };
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [devices, setDevices] = useState<DeviceOption[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null);
  const [targetApp, setTargetApp] = useState('base');
  const [payload, setPayload] = useState('');
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  const fetchDevices = async () => {
    try {
      const [devRes, orchRes] = await Promise.all([
        fetch('/api/v1/souls/devices', { headers }),
        fetch('/api/v1/analytics/orchestrator/list', { headers }),
      ]);
      const dbDevices = devRes.ok ? await devRes.json() : [];
      const orchEmulators: any[] = (orchRes.ok ? await orchRes.json() : { emulators: [] }).emulators || [];
      const merged: DeviceOption[] = []; const seen = new Set<string>();
      for (const d of dbDevices) {
        const orch = orchEmulators.find(e => `localhost:${e.adb_port}` === d.device_id || e.name === d.device_id);
        merged.push({ device_id: d.device_id, vnc_port: orch?.vnc_port || null, status: orch?.status || d.status || null });
        seen.add(d.device_id);
      }
      for (const e of orchEmulators) { const id = `localhost:${e.adb_port}`; if (!seen.has(id)) merged.push({ device_id: id, vnc_port: e.vnc_port, status: e.status }); }
      setDevices(merged);
    } catch (e) { console.error(e); }
  };
  useEffect(() => {
    (async () => { const r = await fetch('/api/v1/souls/profiles', { headers }); if (r.ok) setAgents(await r.json()); })();
    fetchDevices(); const iv = setInterval(fetchDevices, 8000); return () => clearInterval(iv);
  }, []);
  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [logs]);

  const append = (text: string, kind: LogLine['kind'] = 'info') => setLogs(prev => [...prev, { ts: new Date().toLocaleTimeString('ru-RU'), text, kind }]);
  const classify = (l: string): LogLine['kind'] => l.includes('[FAIL]') ? 'fail' : l.includes('[OK]') ? 'ok' : 'info';

  const trigger = async () => {
    if (!selectedAgent || !selectedDevice || !payload.trim()) { append('[FAIL] Нужны агент, устройство и непустой текст.', 'fail'); return; }
    setRunning(true);
    append(`▶ Отправка на ${selectedDevice} как ${selectedAgent} [app=${targetApp}]…`);
    try {
      const r = await fetch('/api/v1/sandbox/execute', { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: selectedAgent, device_id: selectedDevice, target_app: targetApp, text_payload: payload }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) append(`[FAIL] HTTP ${r.status}: ${d.detail || 'запрос отклонён'}`, 'fail');
      else { for (const line of (Array.isArray(d.log) ? d.log : [])) append(line, classify(line)); append(d.success ? '✔ Прогон: УСПЕХ.' : '✖ Прогон: СБОЙ.', d.success ? 'ok' : 'fail'); }
    } catch (e: any) { append(`[FAIL] Сетевая ошибка: ${e.message}`, 'fail'); }
    finally { setRunning(false); }
  };

  const activeDevice = devices.find(d => d.device_id === selectedDevice);
  const vncUrl = activeDevice?.vnc_port ? `http://${window.location.hostname}:${activeDevice.vnc_port}` : null;

  return (
    <Box p="lg">
      <Group mb="md"><Title order={2}><Terminal size={22} style={{ verticalAlign: -4 }} /> Песочница</Title></Group>
      <Text size="sm" c="dimmed" mb="md">Ручной запуск изолированного ввода на устройстве в реальном времени (минуя очереди Redis). Мобильный стек вне scope.</Text>

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <Stack gap="md">
            <Select label="Агент" leftSection={<Smartphone size={15} />} searchable clearable placeholder="выберите агента"
              data={agents.map(a => ({ value: a.agent_id, label: `${a.full_name} (${a.agent_id})` }))} value={selectedAgent} onChange={setSelectedAgent} />
            <Select label="Виртуальное устройство" leftSection={<Cpu size={15} />} searchable clearable placeholder="выберите устройство"
              data={devices.map(d => ({ value: d.device_id, label: `${d.device_id}${d.vnc_port ? ` · VNC ${d.vnc_port}` : ''}${d.status ? ` · ${d.status}` : ''}` }))} value={selectedDevice} onChange={setSelectedDevice} />
            <Box>
              <Text size="sm" mb={6}>Целевое приложение</Text>
              <SegmentedControl value={targetApp} onChange={setTargetApp} data={TARGET_APPS} />
            </Box>
            <Textarea label="Текст для ввода" autosize minRows={4} value={payload} onChange={e => setPayload(e.currentTarget.value)} placeholder="Текст, который физически напечатается на устройстве…" />
            <Button leftSection={<Play size={16} />} loading={running} onClick={trigger}>Запустить выполнение</Button>

            <Paper withBorder radius="md" p="sm" style={{ background: '#0a0d14' }}>
              <Group gap="xs" mb="xs"><Terminal size={14} /><Text size="sm" fw={600}>Журнал выполнения</Text></Group>
              <ScrollArea h={220}>
                {logs.length === 0 ? <Text c="dimmed" size="sm">Ожидание запуска…</Text> : logs.map((l, i) => (
                  <Text key={i} size="xs" ff="monospace" style={{ color: LOG_COLOR[l.kind] }}><Text span c="dimmed">{l.ts}</Text> {l.text}</Text>
                ))}
                <div ref={logEndRef} />
              </ScrollArea>
            </Paper>
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 6 }}>
          <Paper withBorder radius="md" p="sm" h="100%">
            <Group justify="space-between" mb="xs"><Text fw={600}>Прямой VNC-монитор</Text>{activeDevice && <Text size="xs" ff="monospace" c="dimmed">{activeDevice.device_id}</Text>}</Group>
            {vncUrl ? <iframe title="sandbox-vnc" src={vncUrl} style={{ width: '100%', height: 480, border: 'none', borderRadius: 8 }} />
              : <Box style={{ height: 480, display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px dashed var(--border-subtle)', borderRadius: 8 }}><Text c="dimmed" size="sm" ta="center" px="md">{selectedDevice ? 'У выбранного устройства нет доступного WebVNC-порта.' : 'Выберите оркестрированное устройство для трансляции экрана.'}</Text></Box>}
          </Paper>
        </Grid.Col>
      </Grid>
    </Box>
  );
};

export default SandboxConsole;
