import { useState, useEffect, useCallback } from 'react';
import { Box, Group, Title, Text, Badge, Button, Anchor, Notification } from '@mantine/core';
import { Radar, Flame, X, Rocket } from 'lucide-react';
import { DataView, Col } from '../ui/DataView';

export interface MissionPrefill { target_url: string; title: string; narrative_goal: string; }
interface ScoutedTarget {
  id: number; platform: string; url: string; author_name: string | null; content_summary: string | null;
  velocity_score: number; engagement: number; posted_at: number | null; status: string;
}
interface Props { token: string; onConverted: (p: MissionPrefill) => void; }

const heatColor = (score: number): string => {
  const t = Math.max(0, Math.min(1, score / 2000));
  const hue = 50 - 50 * t;
  return `hsl(${hue}, 95%, 52%)`;
};
const relTime = (epoch: number | null): string => {
  if (!epoch) return 'неизвестно';
  const s = Math.max(0, Math.floor(Date.now() / 1000) - epoch);
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`;
  return `${Math.floor(s / 86400)} дн назад`;
};

export default function ScoutingScreen({ token, onConverted }: Props) {
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  const [targets, setTargets] = useState<ScoutedTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ text: string; color: string } | null>(null);
  const flash = (text: string, color: string) => { setToast({ text, color }); setTimeout(() => setToast(null), 4000); };

  const fetchRadar = useCallback(async () => {
    try { const r = await fetch('/api/v1/scouting/radar', { headers }); if (r.ok) setTargets(await r.json()); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { fetchRadar(); const iv = setInterval(fetchRadar, 15000); return () => clearInterval(iv); }, [fetchRadar]);

  const dismiss = async (id: number) => {
    setTargets(prev => prev.filter(t => t.id !== id));
    try { await fetch(`/api/v1/scouting/${id}/dismiss`, { method: 'POST', headers }); } catch { fetchRadar(); }
  };
  const convert = async (t: ScoutedTarget) => {
    try {
      const r = await fetch(`/api/v1/scouting/${t.id}/convert`, { method: 'POST', headers });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Не удалось');
      setTargets(prev => prev.filter(x => x.id !== t.id));
      flash(`Миссия #${d.mission_id} создана — переход в Mission Deck…`, 'teal');
      onConverted({ target_url: d.target_url, title: d.title, narrative_goal: t.content_summary || '' });
    } catch (e: any) { flash(e.message, 'red'); }
  };

  const columns: Col<ScoutedTarget>[] = [
    { key: 'platform', header: 'Платформа', minWidth: 110, render: t => <Badge variant="outline">{t.platform}</Badge> },
    { key: 'author_name', header: 'Автор', minWidth: 150, sortValue: t => (t.author_name || '').toLowerCase(),
      render: t => <Text size="sm" fw={600}>@{t.author_name || 'unknown'}</Text> },
    { key: 'content_summary', header: 'Содержание', minWidth: 380, sortable: false,
      render: t => <Text size="sm" lineClamp={2} maw={460}>{t.content_summary || '(без текста)'}</Text> },
    { key: 'velocity_score', header: 'Скорость', minWidth: 130, align: 'right', sortValue: t => t.velocity_score,
      render: t => <Badge style={{ background: heatColor(t.velocity_score), color: '#1a1a1a' }} leftSection={<Flame size={11} />}>{Math.round(t.velocity_score).toLocaleString('ru-RU')}/ч</Badge> },
    { key: 'engagement', header: 'Вовлечённость', minWidth: 130, align: 'right', sortValue: t => t.engagement,
      render: t => <Text size="sm" c="dimmed">{t.engagement.toLocaleString('ru-RU')}</Text> },
    { key: 'posted_at', header: 'Когда', minWidth: 110, sortValue: t => t.posted_at || 0, render: t => <Text size="xs" c="dimmed">{relTime(t.posted_at)}</Text> },
    { key: 'link', header: '', minWidth: 60, sortable: false, render: t => <Anchor size="xs" href={t.url} target="_blank">↗</Anchor> },
    { key: 'actions', header: 'Действия', minWidth: 200, sortable: false, align: 'right',
      render: t => <Group gap={6} justify="flex-end" onClick={e => e.stopPropagation()}>
        <Button size="xs" variant="subtle" leftSection={<X size={13} />} onClick={() => dismiss(t.id)}>Скрыть</Button>
        <Button size="xs" color="red" variant="light" leftSection={<Rocket size={13} />} onClick={() => convert(t)}>В миссию</Button>
      </Group> },
  ];

  return (
    <Box p="lg">
      {toast && <Notification color={toast.color} onClose={() => setToast(null)} style={{ position: 'fixed', top: 16, right: 16, zIndex: 1000 }}>{toast.text}</Notification>}
      <Group justify="space-between" mb="xs">
        <div>
          <Title order={2}><Radar size={22} style={{ verticalAlign: -4 }} /> Радар разведки</Title>
          <Text size="sm" c="dimmed">Вирусные находки, ранжированные по скорости набора вовлечённости. Сортируйте по «Скорости», превращайте горячий пост в миссию.</Text>
        </div>
      </Group>
      <DataView
        columns={columns} rows={targets} rowKey={t => t.id} loading={loading}
        searchText={t => `${t.platform} ${t.author_name || ''} ${t.content_summary || ''} ${t.url}`}
        searchPlaceholder="🔍 Поиск по автору, тексту, платформе…"
        emptyText="Вирусных целей пока нет."
      />
    </Box>
  );
}
