import { useState, useEffect, useCallback, useMemo, ReactNode } from 'react';
import {
  Box, Group, Stack, Title, Text, Badge, Button, Paper, SimpleGrid, Table, ScrollArea, Divider,
} from '@mantine/core';
import { Users, Link as LinkIcon, Unlink, Radio, History } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';
import { DetailPage } from '../ui/DetailPage';
import { EntityPicker } from '../ui/EntityPicker';
import ChannelManager from './ChannelManager';

interface Account { id: number; agent_id: string | null; platform: string; username: string; status: string; device_id: string | null; }
interface SoulProfile { agent_id: string; codename: string; full_name: string; caste: string; status: string; }
interface AuditLog { id: number; action: string; timestamp: string; }

interface Props { token: string; selectedId: string | null; onOpen: (id: string) => void; onBack: () => void; goTo?: (view: string, id?: string) => void; }

const STATUS_COLOR: Record<string, string> = { active: 'teal', banned: 'red', limited: 'yellow', unbound: 'gray', disabled: 'red', suspended: 'orange' };

export default function AccountsScreen({ token, selectedId, onOpen, onBack, goTo }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [profiles, setProfiles] = useState<SoulProfile[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAccounts = useCallback(async () => {
    setLoading(true);
    try { const r = await fetch('/api/v1/souls/accounts', { headers }); if (r.ok) setAccounts(await r.json()); }
    finally { setLoading(false); }
  }, [token]);
  const fetchProfiles = useCallback(async () => {
    const r = await fetch('/api/v1/souls/profiles', { headers }); if (r.ok) setProfiles(await r.json());
  }, [token]);
  useEffect(() => { fetchAccounts(); fetchProfiles(); }, [fetchAccounts, fetchProfiles]);

  const selected = selectedId ? accounts.find(a => String(a.id) === selectedId) || null : null;

  if (selectedId) {
    if (!selected) return <DetailPage onBack={onBack} title="Загрузка…" subtitle={selectedId}><Text c="dimmed">Аккаунт загружается…</Text></DetailPage>;
    return <AccountDetail key={selected.id} token={token} account={selected} profiles={profiles} onBack={onBack} onChanged={() => { fetchAccounts(); fetchProfiles(); }} goTo={goTo} />;
  }

  const columns: Col<Account>[] = [
    { key: 'username', header: 'Аккаунт', minWidth: 200, sortValue: a => a.username,
      render: a => <Text ff="monospace" fw={600}>{a.username}</Text> },
    { key: 'platform', header: 'Платформа', minWidth: 120, render: a => <Badge variant="outline">{a.platform}</Badge> },
    { key: 'status', header: 'Статус', minWidth: 120, sortValue: a => a.status,
      render: a => <Badge color={STATUS_COLOR[a.status] || 'gray'} variant="light">{a.status}</Badge> },
    { key: 'device_id', header: 'Устройство', minWidth: 140, render: a => a.device_id || '—' },
    { key: 'agent_id', header: 'Привязка', minWidth: 220, sortValue: a => a.agent_id || '',
      render: a => a.agent_id
        ? <Badge variant="light" color="indigo" leftSection={<LinkIcon size={11} />} style={{ cursor: goTo ? 'pointer' : undefined }}
            onClick={goTo ? (e) => { e.stopPropagation(); goTo('souls', a.agent_id!); } : undefined}>{a.agent_id}</Badge>
        : <Text size="sm" c="dimmed"><Unlink size={12} style={{ verticalAlign: -2 }} /> свободен</Text> },
  ];

  return (
    <Box p="lg">
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Users size={22} style={{ verticalAlign: -4 }} /> Аккаунты</Title>
          <Text size="sm" c="dimmed">Реальные TG/соц-аккаунты роя — нажмите на строку, чтобы открыть карточку и управлять привязкой, каналами и историей.</Text>
        </div>
      </Group>
      <DataView
        columns={columns} rows={accounts} rowKey={a => a.id} loading={loading}
        searchText={a => `${a.username} ${a.platform} ${a.status} ${a.agent_id || ''}`}
        searchPlaceholder="🔍 Поиск по имени, платформе, статусу, привязке…"
        emptyText="Аккаунтов нет. Добавьте их через «Фабрику авторизации»."
        onRowClick={a => onOpen(String(a.id))}
      />
    </Box>
  );
}

function AccountDetail({ token, account, profiles, onBack, onChanged, goTo }: {
  token: string; account: Account; profiles: SoulProfile[]; onBack: () => void; onChanged: () => void; goTo?: (view: string, id?: string) => void;
}) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [pickSoul, setPickSoul] = useState(false);
  const [channelMgr, setChannelMgr] = useState(false);

  useEffect(() => {
    (async () => { const r = await fetch(`/api/v1/souls/accounts/${account.id}/history`, { headers }); if (r.ok) setLogs(await r.json()); })();
  }, [account.id]);

  const freeSouls = useMemo(() => profiles, [profiles]);

  const bind = async (agentId: string) => {
    const r = await fetch(`/api/v1/souls/accounts/${account.id}/bind?agent_id=${encodeURIComponent(agentId)}`, { method: 'PUT', headers });
    if (r.ok) onChanged();
  };
  const unbind = async () => {
    const r = await fetch(`/api/v1/souls/accounts/${account.id}/unbind`, { method: 'PUT', headers });
    if (r.ok) onChanged();
  };

  const isTg = account.platform === 'telegram';
  const field = (label: string, value: ReactNode) => (
    <Box><Text size="xs" tt="uppercase" c="dimmed" fw={600}>{label}</Text><Text mt={2}>{value}</Text></Box>
  );

  return (
    <DetailPage
      onBack={onBack}
      title={account.username}
      subtitle={<Text span ff="monospace" size="sm" c="dimmed">{account.platform} · #{account.id}</Text>}
      headerRight={<Badge size="lg" color={STATUS_COLOR[account.status] || 'gray'} variant="light">{account.status}</Badge>}
    >
      <Stack gap="lg" maw={780}>
        <Paper withBorder p="md" radius="md">
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            {field('Платформа', account.platform)}
            {field('Имя', <Text ff="monospace">{account.username}</Text>)}
            {field('Устройство', account.device_id || 'нет')}
            {field('Статус', <Badge color={STATUS_COLOR[account.status] || 'gray'} variant="light">{account.status}</Badge>)}
          </SimpleGrid>
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Text fw={600} mb="sm">Привязка к душе</Text>
          {account.agent_id ? (
            <Group justify="space-between">
              <Group gap="xs">
                <Badge size="lg" variant="light" color="indigo" leftSection={<LinkIcon size={13} />}>{account.agent_id}</Badge>
                {goTo && <Button size="xs" variant="subtle" onClick={() => goTo('souls', account.agent_id!)}>открыть душу →</Button>}
              </Group>
              <Group gap="xs">
                {isTg && <Button variant="default" leftSection={<Radio size={15} />} onClick={() => setChannelMgr(true)}>Каналы аккаунта</Button>}
                <Button variant="light" color="red" leftSection={<Unlink size={15} />} onClick={unbind}>Отвязать душу</Button>
              </Group>
            </Group>
          ) : (
            <Group justify="space-between">
              <Text c="dimmed" size="sm">Аккаунт свободен — привяжите к нему душу из списка.</Text>
              <Button leftSection={<LinkIcon size={15} />} onClick={() => setPickSoul(true)}>Привязать душу</Button>
            </Group>
          )}
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Group gap="xs" mb="sm"><History size={16} /><Text fw={600}>История изменений</Text></Group>
          {logs.length === 0 ? <Text c="dimmed" size="sm">История пуста.</Text> : (
            <ScrollArea.Autosize mah={360}>
              <Table striped>
                <Table.Thead><Table.Tr><Table.Th>Время</Table.Th><Table.Th>Действие</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{logs.map(l => (
                  <Table.Tr key={l.id}><Table.Td c="dimmed">{new Date(l.timestamp).toLocaleString('ru-RU')}</Table.Td><Table.Td>{l.action}</Table.Td></Table.Tr>))}</Table.Tbody>
              </Table>
            </ScrollArea.Autosize>
          )}
        </Paper>
        <Divider />
        <Button variant="default" w={120} onClick={onBack}>Назад</Button>
      </Stack>

      <EntityPicker
        opened={pickSoul} onClose={() => setPickSoul(false)} title="Привязать душу к аккаунту"
        rows={freeSouls} rowKey={s => s.agent_id}
        searchText={s => `${s.full_name} ${s.codename} ${s.agent_id} ${s.caste}`}
        emptyText="Нет доступных душ."
        columns={[
          { key: 'full_name', header: 'Душа', minWidth: 220, render: s => <Stack gap={0}><Text fw={600}>{s.full_name || s.codename}</Text><Text size="xs" c="dimmed" ff="monospace">{s.agent_id}</Text></Stack> },
          { key: 'caste', header: 'Каста', minWidth: 90, render: s => <Badge variant="light">{s.caste}</Badge> },
          { key: 'status', header: 'Статус', minWidth: 120, render: s => <Badge size="sm" variant="light">{s.status}</Badge> },
        ]}
        onPick={s => bind(s.agent_id)}
      />
      {channelMgr && account.agent_id && <ChannelManager token={token} agentId={account.agent_id} label={account.username} onClose={() => setChannelMgr(false)} />}
    </DetailPage>
  );
}
