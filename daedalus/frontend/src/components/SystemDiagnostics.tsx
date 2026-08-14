import { useState, useEffect } from 'react';
import { Box, Group, Text, SimpleGrid, Paper, ThemeIcon } from '@mantine/core';
import { Activity, Server, Zap, Database, Smartphone } from 'lucide-react';

interface DiagnosticsProps { token: string; }
interface LatencyMetrics { daedalus_db: number; orpheus_cache: number; huginn_sync: number; myrmidon_adb: number; }

const latColor = (v: number) => (v < 50 ? 'teal' : v < 100 ? 'yellow' : 'red');

export default function SystemDiagnostics({ token }: DiagnosticsProps) {
  const [lat, setLat] = useState<LatencyMetrics>({ daedalus_db: 0, orpheus_cache: 0, huginn_sync: 0, myrmidon_adb: 0 });

  useEffect(() => {
    const fetchLatency = async () => {
      try {
        const r = await fetch('/api/v1/analytics/latency', { headers: { Authorization: `Bearer ${token}` } });
        if (r.ok) setLat(await r.json());
      } catch (e) { console.error(e); }
    };
    fetchLatency(); const iv = setInterval(fetchLatency, 2000); return () => clearInterval(iv);
  }, [token]);

  const tiles: { label: string; value: number; icon: React.ReactNode }[] = [
    { label: 'DAEDALUS PostgreSQL', value: lat.daedalus_db, icon: <Database size={20} /> },
    { label: 'ORPHEUS · кэш', value: lat.orpheus_cache, icon: <Zap size={20} /> },
    { label: 'HUGINN · цикл синхр.', value: lat.huginn_sync, icon: <Server size={20} /> },
    { label: 'MYRMIDON · ADB-прокси', value: lat.myrmidon_adb, icon: <Smartphone size={20} /> },
  ];

  return (
    <Box>
      <Group gap="xs" mb={4}><Activity size={20} /><Text fw={600} fz="lg">Целостность системы и метрики</Text></Group>
      <Text size="sm" c="dimmed" mb="md">Задержки синхронизации сервисов роя в реальном времени.</Text>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
        {tiles.map(t => {
          const c = latColor(t.value);
          return (
            <Paper key={t.label} withBorder radius="md" p="md" style={{ borderLeft: `3px solid var(--mantine-color-${c}-6)`, background: 'var(--bg-surface)' }}>
              <Group justify="space-between" align="flex-start" wrap="nowrap">
                <Box>
                  <Text size="xs" tt="uppercase" c="dimmed" fw={600}>{t.label}</Text>
                  <Text fz={26} fw={700} c={c} mt={4}>{t.value.toFixed(1)} <Text span fz="sm" c="dimmed">ms</Text></Text>
                </Box>
                <ThemeIcon color={c} variant="light" size="lg">{t.icon}</ThemeIcon>
              </Group>
            </Paper>
          );
        })}
      </SimpleGrid>
    </Box>
  );
}
