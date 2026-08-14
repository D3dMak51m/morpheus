import { useEffect, useState, useRef } from 'react';
import { Box, Group, Title, Text, Tabs, SimpleGrid, Alert, Badge, Paper, Loader, Center } from '@mantine/core';
import { Activity, Server, Database, Radar, ShieldCheck, ShieldAlert, Cpu, MessageSquare, Inbox } from 'lucide-react';
import SystemDiagnostics from './SystemDiagnostics';
import { StatTile } from '../ui/StatTile';

interface DashboardProps { token: string; }

interface Overlord {
  readiness: 'GO' | 'NO-GO';
  blockers: string[];
  missions: { active: number; total: number };
  fleet: { online: number; total: number; recovering: number };
  accounts: { active: number };
  database: { captured_events: number; scouted_pending: number; activity_logs: number;
    retention: { last_run_at: string | null; captured_pruned: number; targets_pruned: number; status: string }; };
  huginn: { scouted_last_hour: number; top_velocity: number };
  timestamp: string;
}
interface Swarm {
  today: Record<string, number>; all_time: Record<string, number>;
  knowledge: { total: number; today: number };
  live: { active_dialogues: number; target_channels: number; news_channels: number };
  agents_by_status: Record<string, number>;
}

const BLOCKER_LABELS: Record<string, string> = {
  database: 'База данных недоступна', fleet_online: 'Нет эмуляторов онлайн',
  accounts_active: 'Нет активных аккаунтов', no_recovering_devices: 'Устройства восстанавливаются',
};

// Keep a small rolling history of a metric across polls → live sparkline.
function useRolling(value: number | undefined, len = 24): number[] {
  const ref = useRef<number[]>([]);
  if (typeof value === 'number') {
    const a = ref.current;
    if (a.length === 0 || a[a.length - 1] !== value) { a.push(value); if (a.length > len) a.shift(); }
  }
  return ref.current;
}

function Overview({ token }: { token: string }) {
  const headers = { Authorization: `Bearer ${token}` };
  const [d, setD] = useState<Overlord | null>(null);
  const [s, setS] = useState<Swarm | null>(null);
  const [error, setError] = useState('');

  const fetchAll = async () => {
    try {
      const [or, sr] = await Promise.all([
        fetch('/api/v1/analytics/overlord', { headers }),
        fetch('/api/v1/analytics/swarm', { headers }),
      ]);
      if (or.ok) setD(await or.json());
      if (sr.ok) setS(await sr.json());
      setError('');
    } catch (e: any) { setError(e.message || 'Не удалось загрузить'); }
  };
  useEffect(() => { fetchAll(); const iv = setInterval(fetchAll, 8000); return () => clearInterval(iv); }, []);

  const sparkScouted = useRolling(d?.huginn.scouted_last_hour);
  const sparkComments = useRolling(s?.today?.comment);
  const sparkDialogues = useRolling(s?.live?.active_dialogues);

  if (error && !d) return <Alert color="red" title="Оверлорд роя недоступен">{error}</Alert>;
  if (!d) return <Center py="xl"><Loader /></Center>;

  const isGo = d.readiness === 'GO';
  const fleetPct = d.fleet.total > 0 ? Math.round((d.fleet.online / d.fleet.total) * 100) : 0;
  const lastPrune = d.database.retention.last_run_at ? new Date(d.database.retention.last_run_at).toLocaleString('ru-RU') : 'никогда';

  return (
    <Box>
      <Alert
        color={isGo ? 'teal' : 'red'}
        variant="light"
        icon={isGo ? <ShieldCheck size={20} /> : <ShieldAlert size={20} />}
        title={
          <Group gap="sm">
            <Text fw={700}>Готовность роя:</Text>
            <Badge color={isGo ? 'teal' : 'red'} size="lg" variant="filled">{isGo ? 'ГОТОВ' : 'НЕ ГОТОВ'}</Badge>
          </Group>
        }
        mb="lg"
      >
        {!isGo && d.blockers.length > 0
          ? <Text>Блокеры: {d.blockers.map(b => BLOCKER_LABELS[b] || b).join(' · ')}</Text>
          : <Text c="dimmed">Все системы в норме — рой готов к работе.</Text>}
      </Alert>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
        <StatTile label="Активные миссии" value={d.missions.active} sub={`всего ${d.missions.total}`}
          icon={<Activity size={22} />} color="#818cf8" />
        <StatTile label="Комментарии сегодня" value={s?.today?.comment ?? '—'} sub={`ответы ${s?.today?.reply ?? 0} · реакции ${s?.today?.react ?? 0}`}
          color="#34c98b" spark={sparkComments} />
        <StatTile label="Активные диалоги" value={s?.live?.active_dialogues ?? '—'} sub={`каналы: цели ${s?.live?.target_channels ?? 0} · новости ${s?.live?.news_channels ?? 0}`}
          icon={<MessageSquare size={22} />} color="#48c6ef" spark={sparkDialogues} />
        <StatTile label="Знания (RAG)" value={(s?.knowledge?.total ?? 0).toLocaleString('ru-RU')} sub={`+${s?.knowledge?.today ?? 0} сегодня`}
          icon={<Database size={22} />} color="#9b87f5" />

        <StatTile label="Эмуляторы онлайн" value={`${d.fleet.online}/${d.fleet.total}`} sub={`${fleetPct}%${d.fleet.recovering ? ` · ${d.fleet.recovering} восстан.` : ''}`}
          icon={<Server size={22} />} color="#f59e0b" />
        <StatTile label="Найдено / за час" value={d.huginn.scouted_last_hour} sub={`пик ${Math.round(d.huginn.top_velocity).toLocaleString('ru-RU')}/ч`}
          color="#f76b8a" spark={sparkScouted} />
        <StatTile label="Перехвачено событий" value={d.database.captured_events.toLocaleString('ru-RU')} sub={`очищено ${lastPrune}`}
          icon={<Inbox size={22} />} color="#3b82f6" />
        <StatTile label="Активные аккаунты" value={d.accounts.active} sub={`агенты: активны ${s?.agents_by_status?.active ?? 0} · пауза ${s?.agents_by_status?.suspended ?? 0}`}
          icon={<ShieldCheck size={22} />} color="#22c55e" />
      </SimpleGrid>

      <Paper withBorder radius="md" p="lg" mt="lg" style={{ background: 'var(--bg-surface)' }}>
        <Group gap="sm" mb={4}><Cpu size={18} /><Text fw={600}>Очередь радара</Text></Group>
        <Group gap="xl">
          <Text size="sm"><Radar size={14} style={{ verticalAlign: -2 }} /> в очереди: <b>{d.database.scouted_pending}</b></Text>
          <Text size="sm"><Activity size={14} style={{ verticalAlign: -2 }} /> действий записано: <b>{d.database.activity_logs.toLocaleString('ru-RU')}</b></Text>
          <Text size="sm" c="dimmed">обновлено: {new Date(d.timestamp).toLocaleTimeString('ru-RU')}</Text>
        </Group>
      </Paper>
    </Box>
  );
}

export default function Dashboard({ token }: DashboardProps) {
  return (
    <Box p="lg">
      <Group justify="space-between" mb="md">
        <div>
          <Title order={2}>Центр управления</Title>
          <Text size="sm" c="dimmed">Состояние и активность роя в реальном времени.</Text>
        </div>
        <Badge variant="dot" color="teal" size="lg">авто-обновление</Badge>
      </Group>

      <Tabs defaultValue="overview">
        <Tabs.List mb="md">
          <Tabs.Tab value="overview">Обзор</Tabs.Tab>
          <Tabs.Tab value="diagnostics">Диагностика</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="overview"><Overview token={token} /></Tabs.Panel>
        <Tabs.Panel value="diagnostics"><SystemDiagnostics token={token} /></Tabs.Panel>
      </Tabs>
    </Box>
  );
}
