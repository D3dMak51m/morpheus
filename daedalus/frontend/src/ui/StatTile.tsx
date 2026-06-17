import { ReactNode } from 'react';
import { Paper, Group, Text, Box } from '@mantine/core';

interface StatTileProps {
  label: ReactNode;
  value: ReactNode;
  icon?: ReactNode;
  sub?: ReactNode;
  /** accent color for the left border / icon */
  color?: string;
  /** small numeric series → inline sparkline */
  spark?: number[];
  onClick?: () => void;
}

function Sparkline({ data, color = 'var(--accent)' }: { data: number[]; color?: string }) {
  if (!data || data.length < 2) return null;
  const w = 96, h = 28;
  const max = Math.max(...data), min = Math.min(...data);
  const span = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / span) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" opacity={0.9} />
    </svg>
  );
}

/** A KPI tile for the command dashboard: label, big value, optional sub + icon + sparkline. */
export function StatTile({ label, value, icon, sub, color = 'var(--accent)', spark, onClick }: StatTileProps) {
  return (
    <Paper
      withBorder radius="md" p="md"
      onClick={onClick}
      style={{
        borderLeft: `3px solid ${color}`,
        cursor: onClick ? 'pointer' : undefined,
        background: 'var(--bg-surface)',
        minWidth: 0,
      }}
    >
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Box style={{ minWidth: 0 }}>
          <Text size="xs" tt="uppercase" c="dimmed" fw={600} style={{ letterSpacing: 0.5 }}>{label}</Text>
          <Text fz={28} fw={700} lh={1.1} mt={4}>{value}</Text>
          {sub && <Text size="xs" c="dimmed" mt={4}>{sub}</Text>}
        </Box>
        <Box style={{ flexShrink: 0, color }}>
          {spark && spark.length > 1 ? <Sparkline data={spark} color={color} /> : icon}
        </Box>
      </Group>
    </Paper>
  );
}

export default StatTile;
