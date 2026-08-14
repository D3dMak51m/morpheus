import { useState, useEffect, useCallback } from 'react';
import {
  Box, Group, Stack, Title, Text, Badge, Paper, SimpleGrid, Table, Modal, Anchor, Loader, Center,
} from '@mantine/core';
import { Users, MessageSquare, ThumbsUp, MessageCircle, Newspaper } from 'lucide-react';
import { StatTile } from '../ui/StatTile';
import { DataView, Col } from '../ui/DataView';

interface SwarmData {
  today: Record<string, number>; all_time: Record<string, number>;
  success_24h: number; failed_24h: number;
  by_caste: Record<string, Record<string, number>>;
  by_agent: Array<{ agent_id: string; caste: string; status: string; comment: number; reply: number; react: number; last_active: string | null }>;
  agents_by_status: Record<string, number>;
  knowledge: { total: number; today: number };
  live: { active_dialogues: number; target_channels: number; news_channels: number };
}
interface ActivityLog { id: number; agent_id: string; action_type: string; target_url: string; text_content: string | null; status: string; created_at: string; }
interface Dialogue { agent_id: string; channel: string; post_id: number; url: string; opponent_id: string | null; depth: number; narrative_goal: string; }

interface Props { token: string; onNavigate?: (view: string) => void; }

const CASTE_COLOR: Record<string, string> = { alpha: 'red', beta: 'blue', gamma: 'green' };
const ACTION_LABEL: Record<string, string> = { comment: 'Комментарии', reply: 'Ответы людям', react: 'Реакции' };

export default function SwarmScreen({ token, onNavigate }: Props) {
  const headers = { Authorization: `Bearer ${token}` };
  const [d, setD] = useState<SwarmData | null>(null);
  const [drill, setDrill] = useState<{ title: string; kind: 'activity' | 'dialogues' } | null>(null);
  const [logs, setLogs] = useState<ActivityLog[]>([]);
  const [dialogues, setDialogues] = useState<Dialogue[]>([]);
  const [drillLoading, setDrillLoading] = useState(false);

  const fetchData = useCallback(async () => {
    const r = await fetch('/api/v1/analytics/swarm', { headers });
    if (r.ok) setD(await r.json());
  }, [token]);
  useEffect(() => { fetchData(); const iv = setInterval(() => { if (!drill) fetchData(); }, 10000); return () => clearInterval(iv); }, [fetchData, drill]);

  const openActivity = async (title: string, opts: { action_type?: string; agent_id?: string; agentIds?: string[] }) => {
    setDrill({ title, kind: 'activity' }); setDrillLoading(true);
    try {
      const p = new URLSearchParams({ since_hours: '24', limit: '150' });
      if (opts.action_type) p.set('action_type', opts.action_type);
      if (opts.agent_id) p.set('agent_id', opts.agent_id);
      const r = await fetch(`/api/v1/analytics/stream?${p}`, { headers });
      let rows: ActivityLog[] = (await r.json()).logs || [];
      if (opts.agentIds) rows = rows.filter(x => opts.agentIds!.includes(x.agent_id));
      setLogs(rows);
    } catch { setLogs([]); }
    setDrillLoading(false);
  };
  const openDialogues = async () => {
    setDrill({ title: 'Активные диалоги', kind: 'dialogues' }); setDrillLoading(true);
    try { const r = await fetch('/api/v1/analytics/dialogues', { headers }); setDialogues((await r.json()).dialogues || []); }
    catch { setDialogues([]); }
    setDrillLoading(false);
  };

  if (!d) return <Center py="xl"><Loader /></Center>;
  const t = d.today || {}, all = d.all_time || {};
  const agentsByCaste = (c: string) => (d.by_agent || []).filter(a => a.caste === c).map(a => a.agent_id);

  const activityCols: Col<ActivityLog>[] = [
    { key: 'created_at', header: 'Время', minWidth: 150, sortValue: l => l.created_at, render: l => <Text size="xs" c="dimmed">{new Date(l.created_at).toLocaleString('ru-RU')}</Text> },
    { key: 'agent_id', header: 'Агент', minWidth: 170, render: l => <Text size="sm" ff="monospace">{l.agent_id}</Text> },
    { key: 'action_type', header: 'Действие', minWidth: 130, render: l => ACTION_LABEL[l.action_type] || l.action_type },
    { key: 'status', header: 'Статус', minWidth: 100, render: l => <Badge color={l.status === 'SUCCESS' ? 'teal' : 'orange'} variant="light">{l.status}</Badge> },
    { key: 'text', header: 'Текст / ссылка', minWidth: 360, sortable: false, render: l => <Stack gap={2}>{l.text_content && <Text size="sm" lineClamp={2} maw={460}>«{l.text_content}»</Text>}{l.target_url && <Anchor size="xs" href={l.target_url} target="_blank">{l.target_url}</Anchor>}</Stack> },
  ];
  const dialogueCols: Col<Dialogue>[] = [
    { key: 'agent_id', header: 'Агент', minWidth: 170, render: dl => <Text size="sm" ff="monospace">{dl.agent_id}</Text> },
    { key: 'channel', header: 'Канал', minWidth: 160, render: dl => `📢 ${dl.channel}` },
    { key: 'depth', header: 'Глубина', minWidth: 90, align: 'right', sortValue: dl => dl.depth },
    { key: 'opponent_id', header: 'Оппонент', minWidth: 140, render: dl => <Text size="sm" c="dimmed">{dl.opponent_id || '—'}</Text> },
    { key: 'goal', header: 'Цель / ссылка', minWidth: 320, sortable: false, render: dl => <Stack gap={2}>{dl.narrative_goal && <Text size="sm">{dl.narrative_goal}</Text>}{dl.url && <Anchor size="xs" href={dl.url} target="_blank">{dl.url}</Anchor>}</Stack> },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="md">
        <div>
          <Title order={2}><Users size={22} style={{ verticalAlign: -4 }} /> Рой</Title>
          <Text size="sm" c="dimmed">Сводка активности роя — нажмите на цифру или строку, чтобы увидеть конкретные записи.</Text>
        </div>
        <Badge variant="dot" color="teal" size="lg">авто-обновление</Badge>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} mb="lg">
        <StatTile label="Комментарии" value={t.comment || 0} sub={`всего: ${all.comment || 0}`} icon={<MessageSquare size={22} />} color="#818cf8" onClick={() => openActivity('Комментарии (24ч)', { action_type: 'comment' })} />
        <StatTile label="Ответы людям" value={t.reply || 0} sub={`всего: ${all.reply || 0}`} icon={<MessageCircle size={22} />} color="#34c98b" onClick={() => openActivity('Ответы людям (24ч)', { action_type: 'reply' })} />
        <StatTile label="Реакции" value={t.react || 0} sub={`всего: ${all.react || 0}`} icon={<ThumbsUp size={22} />} color="#48c6ef" onClick={() => openActivity('Реакции (24ч)', { action_type: 'react' })} />
        <StatTile label="Новости в знания" value={d.knowledge.today} sub={`всего: ${d.knowledge.total}`} icon={<Newspaper size={22} />} color="#9b87f5" onClick={() => onNavigate?.('muninn')} />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }} mb="lg">
        <Paper withBorder p="md" radius="md">
          <Text fw={600} mb="sm">По кастам (24ч) <Text span size="xs" c="dimmed">— клик по строке</Text></Text>
          <Table highlightOnHover>
            <Table.Thead><Table.Tr><Table.Th>Каста</Table.Th><Table.Th ta="right">✉️</Table.Th><Table.Th ta="right">💬</Table.Th><Table.Th ta="right">👍</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{(['alpha', 'beta', 'gamma'] as const).map(c => (
              <Table.Tr key={c} style={{ cursor: 'pointer' }} onClick={() => openActivity(`Каста ${c} (24ч)`, { agentIds: agentsByCaste(c) })}>
                <Table.Td><Badge color={CASTE_COLOR[c]} variant="light">{c}</Badge></Table.Td>
                <Table.Td ta="right">{d.by_caste[c]?.comment ?? 0}</Table.Td>
                <Table.Td ta="right">{d.by_caste[c]?.reply ?? 0}</Table.Td>
                <Table.Td ta="right">{d.by_caste[c]?.react ?? 0}</Table.Td>
              </Table.Tr>))}</Table.Tbody>
          </Table>
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Text fw={600} mb="sm">Сейчас</Text>
          <Stack gap="xs">
            <Group justify="space-between" style={{ cursor: 'pointer' }} onClick={openDialogues}><Text size="sm">🗣 Активные диалоги ›</Text><Text fw={700}>{d.live.active_dialogues}</Text></Group>
            <Group justify="space-between" style={{ cursor: onNavigate ? 'pointer' : undefined }} onClick={() => onNavigate?.('souls')}><Text size="sm">🎯 Целевые каналы {onNavigate ? '›' : ''}</Text><Text fw={700}>{d.live.target_channels}</Text></Group>
            <Group justify="space-between" style={{ cursor: onNavigate ? 'pointer' : undefined }} onClick={() => onNavigate?.('souls')}><Text size="sm">📰 Новостные каналы {onNavigate ? '›' : ''}</Text><Text fw={700}>{d.live.news_channels}</Text></Group>
            <Group justify="space-between"><Text size="sm">🤖 Агенты активны</Text><Text fw={700}>{d.agents_by_status['active'] ?? 0}</Text></Group>
            <Group justify="space-between"><Text size="sm">⏸ На паузе</Text><Text fw={700}>{d.agents_by_status['suspended'] ?? 0}</Text></Group>
            <Group justify="space-between"><Text size="sm">✅ Успех / ❌ сбой (24ч)</Text><Text fw={700}>{d.success_24h} / {d.failed_24h}</Text></Group>
          </Stack>
        </Paper>
      </SimpleGrid>

      <Paper withBorder p="md" radius="md">
        <Text fw={600} mb="sm">По агентам (24ч) <Text span size="xs" c="dimmed">— клик по строке</Text></Text>
        <Table highlightOnHover>
          <Table.Thead><Table.Tr><Table.Th>Агент</Table.Th><Table.Th>Каста</Table.Th><Table.Th>Статус</Table.Th><Table.Th ta="right">✉️</Table.Th><Table.Th ta="right">💬</Table.Th><Table.Th ta="right">👍</Table.Th><Table.Th>Последняя активность</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{d.by_agent.length === 0 ? <Table.Tr><Table.Td colSpan={7}><Text c="dimmed" ta="center" py="sm">За сутки активности нет.</Text></Table.Td></Table.Tr> : d.by_agent.map(a => (
            <Table.Tr key={a.agent_id} style={{ cursor: 'pointer' }} onClick={() => openActivity(`Агент ${a.agent_id} (24ч)`, { agent_id: a.agent_id })}>
              <Table.Td><Text size="sm" ff="monospace">{a.agent_id}</Text></Table.Td>
              <Table.Td><Badge color={CASTE_COLOR[a.caste] || 'gray'} variant="light">{a.caste}</Badge></Table.Td>
              <Table.Td><Badge color={a.status === 'active' ? 'teal' : 'orange'} variant="light">{a.status}</Badge></Table.Td>
              <Table.Td ta="right">{a.comment}</Table.Td><Table.Td ta="right">{a.reply}</Table.Td><Table.Td ta="right">{a.react}</Table.Td>
              <Table.Td><Text size="xs" c="dimmed">{a.last_active ? new Date(a.last_active).toLocaleString('ru-RU') : '—'}</Text></Table.Td>
            </Table.Tr>))}</Table.Tbody>
        </Table>
      </Paper>

      <Modal opened={!!drill} onClose={() => setDrill(null)} title={drill?.title} size="80%" centered withinPortal={false}>
        {drillLoading ? <Center py="xl"><Loader /></Center> : drill?.kind === 'activity'
          ? <DataView columns={activityCols} rows={logs} rowKey={l => l.id} searchText={l => `${l.agent_id} ${ACTION_LABEL[l.action_type] || l.action_type} ${l.text_content || ''} ${l.target_url || ''}`} searchPlaceholder="🔍 Поиск…" emptyText="Записей нет." pageSize={25} />
          : <DataView columns={dialogueCols} rows={dialogues} rowKey={dl => `${dl.agent_id}:${dl.channel}:${dl.post_id}:${dl.depth}`} searchText={dl => `${dl.agent_id} ${dl.channel} ${dl.opponent_id || ''} ${dl.narrative_goal || ''}`} searchPlaceholder="🔍 Поиск…" emptyText="Активных диалогов нет." pageSize={25} />}
      </Modal>
    </Box>
  );
}
