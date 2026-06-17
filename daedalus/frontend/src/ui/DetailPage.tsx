import { ReactNode } from 'react';
import { ActionIcon, Box, Group, Stack, Text, Title } from '@mantine/core';
import { ArrowLeft } from 'lucide-react';

interface DetailPageProps {
  /** Back to the list. */
  onBack: () => void;
  backLabel?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  /** Right-aligned header content (status badge, quick actions). */
  headerRight?: ReactNode;
  /** Sticky bottom action bar (Cancel / Save). */
  footer?: ReactNode;
  children: ReactNode;
}

/**
 * Full-screen master→detail scaffold. The operator mandate is that editing a selected
 * entity is a dedicated screen (not a modal/drawer) exposing all of its parameters — this
 * is the shared shell: a back affordance, a header (title/subtitle + actions), a scrollable
 * body, and a sticky footer save bar.
 */
export function DetailPage({ onBack, backLabel = 'Назад', title, subtitle, headerRight, footer, children }: DetailPageProps) {
  return (
    <Box style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap"
        style={{ padding: '20px 28px 16px', borderBottom: '1px solid var(--border-subtle)', position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg-base)' }}>
        <Group align="flex-start" wrap="nowrap" gap="sm">
          <ActionIcon variant="default" size="lg" onClick={onBack} aria-label={backLabel} title={backLabel}>
            <ArrowLeft size={18} />
          </ActionIcon>
          <Stack gap={2}>
            <Title order={2} style={{ lineHeight: 1.1 }}>{title}</Title>
            {subtitle && <Text size="sm" c="dimmed">{subtitle}</Text>}
          </Stack>
        </Group>
        {headerRight && <Group gap="xs" wrap="nowrap">{headerRight}</Group>}
      </Group>

      <Box style={{ flex: 1, padding: '20px 28px 28px' }}>{children}</Box>

      {footer && (
        <Box style={{ position: 'sticky', bottom: 0, zIndex: 5, padding: '14px 28px', borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}>
          <Group justify="flex-end" gap="sm">{footer}</Group>
        </Box>
      )}
    </Box>
  );
}

export default DetailPage;
