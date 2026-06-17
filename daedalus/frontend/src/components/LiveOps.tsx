import { useState, useEffect, useRef, useCallback } from 'react';
import { Box, Group, Stack, Title, Text, Badge, Button, Paper, ScrollArea, Grid } from '@mantine/core';

interface AgentEvent { id: string; agent_id: string; service: string; event: string; detail: string; status: string; target: string; ts: string; }
interface AgentStatus { agent_id: string; event: string; detail: string; status: string; target: string; service: string; ts: string; id: string; }
interface LiveOpsProps { token: string; }

const EVENT_META: Record<string, { icon: string; label: string }> = {
  poll: { icon: '🔄', label: 'Поллинг диалогов' }, target_scan: { icon: '🎯', label: 'Сканирует каналы' },
  target_post: { icon: '🎯', label: 'Релевантный пост в цели' }, news_ingest: { icon: '🗞', label: 'Загрузил новости в знания' },
  amplify: { icon: '📣', label: 'Рой усиливает (alpha→beta/gamma)' }, reacted: { icon: '👍', label: 'Гамма поставил реакцию' },
  cooldown: { icon: '🧊', label: 'Пауза (лимит Telegram)' }, account_alert: { icon: '🚫', label: 'Проблема с аккаунтом' },
  reply_detected: { icon: '👋', label: 'Получен ответ человека' }, reading_post: { icon: '👁', label: 'Читает пост' },
  reading_thread: { icon: '🌡', label: 'Оценивает настроение треда' }, reading_news: { icon: '📰', label: 'Читает новость' },
  thinking: { icon: '💭', label: 'Сочиняет' }, memory_read: { icon: '🧠', label: 'Вспоминает' },
  memory_write: { icon: '🧠', label: 'Обновляет память' }, generated: { icon: '✨', label: 'Готов текст' },
  guardrail_reject: { icon: '♻️', label: 'Переписывает черновик' }, posting: { icon: '✍️', label: 'Публикует' },
  commented: { icon: '✉️', label: 'Опубликовал комментарий' }, replied: { icon: '💬', label: 'Ответил человеку' },
  dispatched: { icon: '📤', label: 'Отправил в исполнение' }, error: { icon: '⚠️', label: 'Ошибка' },
};
const AGENT_COLORS = ['#5b8def', '#34c98b', '#e0a23c', '#c850c0', '#48c6ef', '#f76b8a', '#9b87f5', '#ff9f43'];
function agentColor(id: string): string { let h = 0; for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0; return AGENT_COLORS[h % AGENT_COLORS.length]; }
function eventMeta(ev: string) { return EVENT_META[ev] || { icon: '•', label: ev }; }
function fmtTime(ts: string): string { const t = parseFloat(ts); if (!t) return ''; return new Date(t * 1000).toLocaleTimeString('ru-RU', { hour12: false }); }
function liveness(a: AgentStatus): 'working' | 'idle' | 'asleep' { const age = Date.now() - parseFloat(a.ts) * 1000; if (a.status === 'active' && age < 90_000) return 'working'; if (age < 300_000) return 'idle'; return 'asleep'; }
const LIVE_COLOR = { working: '#34c98b', idle: '#e0a23c', asleep: '#475569' } as const;
const LIVE_LABEL = { working: 'работает', idle: 'ожидает', asleep: 'спит' } as const;
const POLL_MS = 1200;
const MAX_EVENTS = 400;

const LiveOps = ({ token }: LiveOpsProps) => {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const lastId = useRef<string>('0');
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const poll = useCallback(async () => {
    if (pausedRef.current) return;
    try {
      const after = lastId.current && lastId.current !== '0' ? `&after=${encodeURIComponent(lastId.current)}` : '';
      const res = await fetch(`/api/v1/analytics/live?limit=300${after}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConnected(true); setError('');
      if (data.last_id && data.last_id !== '0') lastId.current = data.last_id;
      setAgents(data.agents || []);
      const fresh: AgentEvent[] = data.events || [];
      if (fresh.length) { const reversed = [...fresh].reverse(); setEvents(prev => [...reversed, ...prev].slice(0, MAX_EVENTS)); }
    } catch (e: any) { setConnected(false); setError(e?.message || 'ошибка потока'); }
  }, [token]);
  useEffect(() => { poll(); const iv = setInterval(poll, POLL_MS); return () => clearInterval(iv); }, [poll]);

  const filtered = selected ? events.filter(e => e.agent_id === selected) : events;
  type G = AgentEvent & { count: number };
  const shown: G[] = [];
  for (const e of filtered) {
    const last = shown[shown.length - 1];
    if (last && last.agent_id === e.agent_id && last.event === e.event && last.detail === e.detail) last.count += 1;
    else shown.push({ ...e, count: 1 });
  }
  const sortedAgents = [...agents].sort((a, b) => {
    const o = { working: 0, idle: 1, asleep: 2 } as const;
    return o[liveness(a)] - o[liveness(b)] || a.agent_id.localeCompare(b.agent_id);
  });

  const statusBadge = paused
    ? <Badge color="gray" variant="dot">ПАУЗА</Badge>
    : connected ? <Badge color="teal" variant="dot">В ЭФИРЕ</Badge> : <Badge color="red" variant="dot">НЕТ СВЯЗИ</Badge>;

  return (
    <Box p="lg">
      <Group justify="space-between" mb="md">
        <Group gap="sm"><Title order={2}>Командный центр</Title>{statusBadge}</Group>
        <Group gap="xs">
          {selected && <Button size="xs" variant="light" onClick={() => setSelected(null)}>фильтр: {selected} ✕</Button>}
          <Button size="xs" variant="default" onClick={() => setPaused(p => !p)}>{paused ? '▶ Возобновить' : '⏸ Пауза'}</Button>
        </Group>
      </Group>
      {error && <Text c="red" size="sm" mb="sm">Поток недоступен: {error}</Text>}

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Paper withBorder radius="md" p="xs">
            <Text size="xs" tt="uppercase" c="dimmed" fw={600} mb="xs" px="xs">Агенты ({agents.length})</Text>
            <ScrollArea.Autosize mah="72vh">
              {sortedAgents.length === 0 && <Text size="sm" c="dimmed" px="xs">Тихо. Никто пока не активен.</Text>}
              <Stack gap={4}>{sortedAgents.map(a => {
                const lv = liveness(a); const meta = eventMeta(a.event);
                return (
                  <Paper key={a.agent_id} withBorder={selected === a.agent_id} p="xs" radius="sm"
                    style={{ cursor: 'pointer', background: selected === a.agent_id ? 'var(--bg-card)' : undefined }}
                    onClick={() => setSelected(selected === a.agent_id ? null : a.agent_id)}>
                    <Group justify="space-between" wrap="nowrap" gap="xs">
                      <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                        <Box style={{ width: 8, height: 8, borderRadius: 999, background: agentColor(a.agent_id), flexShrink: 0 }} />
                        <Box style={{ minWidth: 0 }}>
                          <Text size="sm" ff="monospace" truncate>{a.agent_id}</Text>
                          <Text size="xs" c="dimmed" truncate>{meta.icon} {a.detail || meta.label}</Text>
                        </Box>
                      </Group>
                      <Badge size="xs" variant="light" style={{ color: LIVE_COLOR[lv], flexShrink: 0 }}>{LIVE_LABEL[lv]}</Badge>
                    </Group>
                  </Paper>
                );
              })}</Stack>
            </ScrollArea.Autosize>
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 8 }}>
          <Paper withBorder radius="md" p="xs">
            <ScrollArea.Autosize mah="72vh">
              {shown.length === 0 ? (
                <Stack gap={4} p="md"><Text>Лента пуста.</Text><Text size="sm" c="dimmed">Как только бот начнёт работать — события появятся здесь в реальном времени.</Text></Stack>
              ) : <Stack gap={2}>{shown.map(e => {
                const meta = eventMeta(e.event);
                return (
                  <Group key={e.id} gap="xs" wrap="nowrap" px="xs" py={4} style={{ borderLeft: `2px solid ${e.status === 'active' ? '#34c98b' : 'transparent'}` }}>
                    <Text size="xs" c="dimmed" ff="monospace" style={{ flexShrink: 0 }}>{fmtTime(e.ts)}</Text>
                    <Text size="sm" style={{ flexShrink: 0 }}>{meta.icon}</Text>
                    <Badge size="xs" variant="outline" style={{ color: agentColor(e.agent_id), borderColor: agentColor(e.agent_id), flexShrink: 0 }}>{e.agent_id}</Badge>
                    <Text size="sm" style={{ flex: 1, minWidth: 0 }} truncate>{e.detail || meta.label}</Text>
                    {e.count > 1 && <Badge size="xs" variant="light" color="gray">×{e.count}</Badge>}
                    {e.target && <Text size="xs" c="dimmed" truncate maw={200} title={e.target}>{e.target}</Text>}
                  </Group>
                );
              })}</Stack>}
            </ScrollArea.Autosize>
          </Paper>
        </Grid.Col>
      </Grid>
    </Box>
  );
};

export default LiveOps;
