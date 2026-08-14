import { useEffect, useRef, useState } from 'react';
import {
  Box, Group, Stack, Title, Text, Badge, Button, NumberInput, Select, TextInput, Paper, SimpleGrid,
  Progress, ScrollArea, ThemeIcon,
} from '@mantine/core';
import { Factory, Cpu, Bot, Rocket, CheckCircle2, XCircle } from 'lucide-react';

interface CloneFactoryProps { token: string; }
interface BotState { index: number; stage: string; status: string; agent_id: string | null; device_id: string | null; phone: string | null; account_id: number | null; error: string | null; }
interface Job { job_id: string; status: string; params: { count: number; caste: string; target_platform: string; vector_focus: string }; log: string[]; bots: BotState[]; summary?: { bound: number; failed: number; total: number }; }

const CASTES = ['alpha', 'beta', 'gamma'];
const PLATFORMS = ['instagram', 'telegram', 'twitter', 'threads', 'youtube'];
const STAGE_TO_STEP: Record<string, number> = { queued: 0, generating_persona: 0, registering: 1, binding: 2, bound: 3, failed: -1 };
const STAGE_LABEL: Record<string, string> = { queued: 'В очереди', generating_persona: 'Генерация персоны…', registering: 'Регистрация · SMS/OTP…', binding: 'Привязка к душе…', bound: 'Привязан ✓', failed: 'Сбой' };

const CloneFactory = ({ token }: CloneFactoryProps) => {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [count, setCount] = useState(5);
  const [caste, setCaste] = useState('beta');
  const [platform, setPlatform] = useState('instagram');
  const [vectorFocus, setVectorFocus] = useState('');
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const pollJob = (jobId: string) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/factory/jobs/${jobId}`, { headers });
        if (res.ok) {
          const data: Job = await res.json();
          setJob(data);
          if (data.status === 'completed' && pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
        }
      } catch (e) { console.error(e); }
    }, 2000);
  };

  const launch = async () => {
    if (!vectorFocus.trim()) { setError('Укажите вектор фокуса.'); return; }
    setLaunching(true); setError('');
    try {
      const res = await fetch('/api/v1/factory/mass-provision', { method: 'POST', headers, body: JSON.stringify({ count, caste, target_platform: platform, vector_focus: vectorFocus }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setJob({ job_id: data.job_id, status: data.status, params: { count, caste, target_platform: platform, vector_focus: vectorFocus }, log: [], bots: data.bots });
      pollJob(data.job_id);
    } catch (e: any) { setError(e.message || 'Не удалось запустить фабрику'); }
    finally { setLaunching(false); }
  };

  const jobRunning = job && job.status !== 'completed';

  return (
    <Box p="lg">
      <Group mb="md"><Title order={2}><Factory size={22} style={{ verticalAlign: -4 }} /> Фабрика клонов</Title></Group>
      <Text size="sm" c="dimmed" mb="md">Автономное массовое создание ботов — запуск AVD, синтез душ, регистрация и привязка. (Мобильный стек вне scope.)</Text>

      <Paper withBorder radius="md" p="lg" maw={760} mb="lg">
        <Stack gap="md">
          <NumberInput label="Число ботов" min={1} max={20} value={count} onChange={v => setCount(Math.max(1, Math.min(20, Number(v) || 1)))} disabled={!!jobRunning} w={200} />
          <Group grow>
            <Select label="Каста" data={CASTES} value={caste} onChange={v => v && setCaste(v)} disabled={!!jobRunning} />
            <Select label="Платформа" data={PLATFORMS} value={platform} onChange={v => v && setPlatform(v)} disabled={!!jobRunning} />
          </Group>
          <TextInput label="Вектор фокуса" value={vectorFocus} onChange={e => setVectorFocus(e.currentTarget.value)} disabled={!!jobRunning} placeholder="напр. гражданский активист, Ташкент, городское развитие" />
          {error && <Text c="red" size="sm">{error}</Text>}
          <Button size="md" leftSection={<Rocket size={18} />} loading={launching || !!jobRunning} onClick={launch}>
            {jobRunning ? 'Создание…' : `Создать ${count} ${count > 1 ? 'ботов' : 'бота'}`}
          </Button>
        </Stack>
      </Paper>

      {job && (
        <Box>
          <Group mb="sm">
            <Cpu size={18} /><Text fw={600}>Монитор выполнения</Text>
            <Badge variant="light">{job.status.replace(/_/g, ' ')}</Badge>
            {job.summary && <Text size="sm" c="dimmed">{job.summary.bound} привязано · {job.summary.failed} сбой / {job.summary.total}</Text>}
          </Group>
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
            {job.bots.map(bot => {
              const step = STAGE_TO_STEP[bot.stage] ?? 0;
              const failed = bot.stage === 'failed' || bot.status === 'failed';
              const done = bot.stage === 'bound' || bot.status === 'done';
              return (
                <Paper key={bot.index} withBorder radius="md" p="md" style={{ borderColor: failed ? 'var(--mantine-color-red-7)' : done ? 'var(--mantine-color-teal-7)' : undefined }}>
                  <Group justify="space-between" mb="xs">
                    <Group gap={6}><Bot size={14} /><Text fw={600} size="sm">Бот {bot.index}</Text></Group>
                    {done && <ThemeIcon color="teal" variant="light" size="sm"><CheckCircle2 size={14} /></ThemeIcon>}
                    {failed && <ThemeIcon color="red" variant="light" size="sm"><XCircle size={14} /></ThemeIcon>}
                  </Group>
                  <Progress value={failed ? 100 : ((step + (done ? 1 : 0)) / 4) * 100} color={failed ? 'red' : done ? 'teal' : 'indigo'} size="sm" mb="xs" />
                  <Text size="xs" c={failed ? 'red' : 'dimmed'}>{STAGE_LABEL[bot.stage] || bot.stage}</Text>
                  {bot.agent_id && <Text size="xs" c="dimmed">душа: {bot.agent_id}</Text>}
                  {bot.phone && <Text size="xs" c="dimmed">телефон: {bot.phone}</Text>}
                  {bot.error && <Text size="xs" c="red">{bot.error}</Text>}
                </Paper>
              );
            })}
          </SimpleGrid>
          {job.log.length > 0 && (
            <Paper withBorder radius="md" p="sm" mt="md" style={{ background: '#0a0d14' }}>
              <Text size="sm" fw={600} mb="xs">Журнал фабрики</Text>
              <ScrollArea h={180}>{job.log.map((line, i) => <Text key={i} size="xs" ff="monospace" c="dimmed">{line}</Text>)}</ScrollArea>
            </Paper>
          )}
        </Box>
      )}
    </Box>
  );
};

export default CloneFactory;
