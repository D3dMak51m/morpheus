/**
 * Left column — лента активностей: every state transition inside the polygon
 * (created / generated / published / scheduled / error / done) with filters by
 * event kind and status. Clicking an event jumps to the post it happened on.
 */
import { ActionIcon, Badge, Box, Group, Paper, ScrollArea, Stack, Text, Tooltip } from '@mantine/core';
import { Activity, RefreshCw, Trash2 } from 'lucide-react';
import { KIND_LABEL, STATUS_COLOR, STATUS_LABEL, SimEvent, timeAgo } from './api';

const KIND_FILTERS = ['comment', 'post', 'reaction', 'generation', 'mission', 'agent', 'account', 'channel', 'knowledge', 'landscape', 'system'];
const STATUS_FILTERS = ['published', 'generated', 'draft', 'scheduled', 'error', 'done'];

interface Props {
  events: SimEvent[];
  kinds: string[];
  statuses: string[];
  onKinds: (v: string[]) => void;
  onStatuses: (v: string[]) => void;
  onOpenPost: (postId: number) => void;
  onRefresh: () => void;
  onClear: () => void;
}

function Chip({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <Badge
      size="sm"
      variant={active ? 'filled' : 'outline'}
      color={active ? 'indigo' : 'gray'}
      style={{ cursor: 'pointer' }}
      onClick={onClick}
    >
      {label}
    </Badge>
  );
}

export default function ActivityFeed({
  events, kinds, statuses, onKinds, onStatuses, onOpenPost, onRefresh, onClear,
}: Props) {
  const toggle = (list: string[], value: string, setter: (v: string[]) => void) =>
    setter(list.includes(value) ? list.filter(x => x !== value) : [...list, value]);

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Group justify="space-between" p="sm" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        <Group gap={6}>
          <Activity size={16} />
          <Text fw={600} size="sm">Лента активностей</Text>
        </Group>
        <Group gap={4}>
          <Tooltip label="Обновить"><ActionIcon variant="subtle" size="sm" onClick={onRefresh}><RefreshCw size={14} /></ActionIcon></Tooltip>
          <Tooltip label="Очистить журнал"><ActionIcon variant="subtle" size="sm" color="red" onClick={onClear}><Trash2 size={14} /></ActionIcon></Tooltip>
        </Group>
      </Group>

      <Stack gap={6} p="sm" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        <Text size="xs" c="dimmed">События</Text>
        <Group gap={4}>
          {KIND_FILTERS.map(k => (
            <Chip key={k} label={KIND_LABEL[k] || k} active={kinds.includes(k)}
              onClick={() => toggle(kinds, k, onKinds)} />
          ))}
        </Group>
        <Text size="xs" c="dimmed" mt={4}>Статусы</Text>
        <Group gap={4}>
          {STATUS_FILTERS.map(s => (
            <Chip key={s} label={STATUS_LABEL[s] || s} active={statuses.includes(s)}
              onClick={() => toggle(statuses, s, onStatuses)} />
          ))}
        </Group>
      </Stack>

      <ScrollArea style={{ flex: 1 }} type="hover">
        <Stack gap={4} p="xs">
          {events.length === 0 && (
            <Text size="sm" c="dimmed" ta="center" py="lg">Событий пока нет.</Text>
          )}
          {events.map(e => (
            <Paper
              key={e.id} withBorder p="xs" radius="sm"
              style={{ cursor: e.post_id ? 'pointer' : 'default', borderLeft: `3px solid var(--mantine-color-${STATUS_COLOR[e.status] || 'gray'}-6)` }}
              onClick={() => e.post_id && onOpenPost(e.post_id)}
            >
              <Group gap={4} mb={2} wrap="nowrap">
                <Badge size="xs" variant="light" color={STATUS_COLOR[e.status] || 'gray'}>
                  {STATUS_LABEL[e.status] || e.status}
                </Badge>
                <Badge size="xs" variant="outline" color="gray">{KIND_LABEL[e.kind] || e.kind}</Badge>
                <Text size="xs" c="dimmed" ml="auto" style={{ whiteSpace: 'nowrap' }}>{timeAgo(e.created_at)}</Text>
              </Group>
              <Text size="sm" lineClamp={3}>{e.summary}</Text>
              {e.actor_label && (
                <Text size="xs" c="dimmed" mt={2}>
                  {e.actor_kind === 'persona' ? '🤖 ' : e.actor_kind === 'account' ? '👤 ' : ''}{e.actor_label}
                </Text>
              )}
            </Paper>
          ))}
        </Stack>
      </ScrollArea>
    </Box>
  );
}
