import { useState } from 'react';
import {
  Box, Grid, Group, Stack, Title, Text, Badge, Button, SegmentedControl, TextInput, Textarea,
  Chip, Paper, Center, Loader, ThemeIcon,
} from '@mantine/core';
import { Dna, Sparkles, Bot, Rocket } from 'lucide-react';

interface ProfileResponse { id: number; agent_id: string; codename: string; caste: string; full_name: string; profession?: string; core_mission?: string; }
const PLATFORMS = ['telegram', 'instagram', 'twitter', 'threads', 'youtube'];

export function SoulGenesisView({ token }: { token: string | null }) {
  const [caste, setCaste] = useState<'alpha' | 'beta' | 'gamma'>('alpha');
  const [agentId, setAgentId] = useState('');
  const [codename, setCodename] = useState('');
  const [focus, setFocus] = useState('');
  const [platforms, setPlatforms] = useState<string[]>(['telegram']);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ProfileResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = (p: string) => setPlatforms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]);

  const synthesize = async () => {
    if (!agentId || !codename || !focus) { setError('Заполните Agent ID, Codename и Фокус.'); return; }
    setLoading(true); setError(null); setResult(null);
    try {
      const res = await fetch('/api/v1/souls/genesis', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ caste, agent_id: agentId, codename, focus, platforms }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Синтез не удался');
      setResult(data);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <Box p="lg">
      <Group justify="space-between" mb="md">
        <div>
          <Title order={2}><Dna size={22} style={{ verticalAlign: -4 }} /> Генезис душ</Title>
          <Text size="sm" c="dimmed">Синтез новой персоны из концепта (qwen2.5:3b). Заполните вектор — движок соберёт полный профиль.</Text>
        </div>
      </Group>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Paper withBorder p="lg" radius="md" pos="relative">
            {loading && (
              <Center pos="absolute" inset={0} style={{ background: 'rgba(7,8,15,0.7)', zIndex: 5, borderRadius: 8 }}>
                <Stack align="center" gap={6}><Loader /><Text c="indigo" fw={700}>Синтез сознания…</Text><Text size="xs" c="dimmed">Подключение к Ollama (qwen2.5:3b)…</Text></Stack>
              </Center>
            )}
            <Stack gap="md">
              <Box>
                <Text size="xs" tt="uppercase" c="dimmed" fw={600} mb={6}>Каста</Text>
                <SegmentedControl fullWidth value={caste} onChange={v => setCaste(v as any)} data={['alpha', 'beta', 'gamma']} />
                <Text size="xs" c="dimmed" mt={6}>{caste === 'alpha' ? 'Глубокий синтез: полный биографический и психологический профиль.' : 'Быстрый шаблон: правила поведения для роёв усиления.'}</Text>
              </Box>
              <Group grow>
                <TextInput label="Agent ID (система)" value={agentId} onChange={e => setAgentId(e.currentTarget.value)} placeholder="agent_omega_9" />
                <TextInput label="Codename" value={codename} onChange={e => setCodename(e.currentTarget.value)} placeholder="Omega" />
              </Group>
              <Textarea label="Вектор / характер" autosize minRows={4} value={focus} onChange={e => setFocus(e.currentTarget.value)}
                placeholder="напр. урбанист из Самарканда, за сохранение исторического облика, критичен к стеклянным новостройкам. Академичный, но саркастичный тон." />
              <Box>
                <Text size="sm" mb={6}>Платформы</Text>
                <Group gap="xs">{PLATFORMS.map(p => <Chip key={p} checked={platforms.includes(p)} onChange={() => toggle(p)}>{p}</Chip>)}</Group>
              </Box>
              {error && <Text c="red" size="sm">{error}</Text>}
              <Button size="md" leftSection={<Rocket size={18} />} loading={loading} onClick={synthesize}>Запустить генезис</Button>
            </Stack>
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Paper withBorder p="lg" radius="md" h="100%">
            <Group gap="xs" mb="md"><Sparkles size={16} /><Text fw={600}>Результат синтеза</Text></Group>
            {result ? (
              <Stack gap="md">
                <Center><Stack align="center" gap={4}>
                  <ThemeIcon size={64} radius="xl" color="indigo" variant="light"><Bot size={32} /></ThemeIcon>
                  <Text fw={700} fz="lg">{result.full_name}</Text>
                  <Badge color="indigo" variant="light">{result.caste.toUpperCase()}</Badge>
                </Stack></Center>
                <Paper withBorder p="sm" radius="md"><Text size="sm"><Text span c="dimmed">ID:</Text> <Text span ff="monospace" c="blue">{result.agent_id}</Text></Text></Paper>
                <Paper withBorder p="sm" radius="md"><Text size="sm"><Text span c="dimmed">Профессия:</Text> {result.profession || '—'}</Text></Paper>
                <Paper withBorder p="sm" radius="md"><Text size="xs" c="dimmed" mb={4}>Главная миссия</Text><Text size="sm">{result.core_mission || '—'}</Text></Paper>
                <Text ta="center" size="xs" c="dimmed">Сохранено в БД (ID: {result.id})</Text>
              </Stack>
            ) : (
              <Center h={300}><Stack align="center" gap={6} c="dimmed"><Dna size={40} /><Text size="sm">Ожидание вектора</Text></Stack></Center>
            )}
          </Paper>
        </Grid.Col>
      </Grid>
    </Box>
  );
}
