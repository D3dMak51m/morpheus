/**
 * Centre column — feed mode: the selected channel's posts, Telegram-style
 * (channel as the author, media, reaction row, "открыть комментарии").
 */
import { ActionIcon, Avatar, Badge, Box, Button, Group, Menu, Paper, ScrollArea, Stack, Text, Title } from '@mantine/core';
import { Image as ImageIcon, Link2, FileText, Mic, Video, MessageSquare, MoreVertical, Pencil, Plus, Pin, Trash2 } from 'lucide-react';
import { REACTION_SET, SimChannel, SimMedia, SimPost, initialsOf, timeAgo } from './api';

const MEDIA_ICON: Record<string, any> = {
  image: ImageIcon, video: Video, audio: Mic, document: FileText, link: Link2,
};

export function MediaRow({ media }: { media: SimMedia[] }) {
  if (!media || media.length === 0) return null;
  return (
    <Stack gap={4} my={6}>
      {media.map((m, i) => {
        const Icon = MEDIA_ICON[m.kind] || FileText;
        const isImage = m.kind === 'image' && m.url && /^https?:\/\//.test(m.url);
        return (
          <Paper key={i} withBorder p={6} radius="sm" style={{ background: 'var(--bg-card)' }}>
            <Group gap={6} wrap="nowrap" align="flex-start">
              <Icon size={15} style={{ marginTop: 2, flexShrink: 0 }} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <Text size="xs" fw={600}>{m.name || m.kind}</Text>
                {m.caption && <Text size="xs" c="dimmed">{m.caption}</Text>}
                {m.url && <Text size="xs" c="dimmed" truncate>{m.url}</Text>}
                {isImage && (
                  <img src={m.url} alt={m.caption || ''} loading="lazy"
                    style={{ maxWidth: '100%', maxHeight: 260, borderRadius: 6, marginTop: 6 }}
                    onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
                )}
              </div>
            </Group>
          </Paper>
        );
      })}
    </Stack>
  );
}

export function ReactionRow({ reactions, onReact }: {
  reactions: Record<string, number>; onReact: (emoji: string) => void;
}) {
  const entries = Object.entries(reactions || {});
  return (
    <Group gap={4} mt={6}>
      {entries.map(([emoji, count]) => (
        <Badge key={emoji} size="lg" variant="light" style={{ cursor: 'pointer' }}
          onClick={() => onReact(emoji)}>{emoji} {count}</Badge>
      ))}
      <Menu shadow="md" position="top-start">
        <Menu.Target>
          <ActionIcon variant="subtle" size="sm" title="Добавить реакцию">＋</ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          <Group gap={2} p={4}>
            {REACTION_SET.map(e => (
              <ActionIcon key={e} variant="subtle" onClick={() => onReact(e)}>{e}</ActionIcon>
            ))}
          </Group>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}

interface Props {
  channel: SimChannel | null;
  posts: SimPost[];
  onOpenThread: (post: SimPost) => void;
  onEditPost: (post: SimPost) => void;
  onDeletePost: (post: SimPost) => void;
  onNewPost: () => void;
  onEditChannel: () => void;
  onReactPost: (post: SimPost, emoji: string) => void;
}

export default function ChannelFeed({
  channel, posts, onOpenThread, onEditPost, onDeletePost, onNewPost, onEditChannel, onReactPost,
}: Props) {
  if (!channel) {
    return (
      <Box p="xl" ta="center">
        <Text c="dimmed">Выберите канал справа или создайте новый — полигон пока пуст.</Text>
      </Box>
    );
  }

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Group justify="space-between" p="sm" wrap="nowrap"
        style={{ borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}>
        <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
          <Avatar color={channel.avatar_color} radius="xl">{initialsOf(channel.title)}</Avatar>
          <div style={{ minWidth: 0 }}>
            <Title order={4} style={{ lineHeight: 1.1 }}>{channel.title}</Title>
            <Text size="xs" c="dimmed" truncate>
              {channel.username} · {channel.subscribers.toLocaleString('ru-RU')} подписчиков
              {channel.geo_label ? ` · ${channel.geo_label}` : ''} · {channel.posts_count} постов
            </Text>
          </div>
        </Group>
        <Group gap={6} wrap="nowrap">
          <Button size="xs" variant="default" leftSection={<Pencil size={13} />} onClick={onEditChannel}>Канал</Button>
          <Button size="xs" leftSection={<Plus size={14} />} onClick={onNewPost}>Пост</Button>
        </Group>
      </Group>

      <ScrollArea style={{ flex: 1 }} type="hover">
        <Stack gap="sm" p="md">
          {posts.length === 0 && (
            <Paper withBorder p="lg" radius="md" ta="center">
              <Text c="dimmed" mb="sm">В канале ещё нет постов.</Text>
              <Button size="xs" leftSection={<Plus size={14} />} onClick={onNewPost}>Создать первый пост</Button>
            </Paper>
          )}
          {posts.map(post => (
            <Paper key={post.id} withBorder p="sm" radius="md" style={{ background: 'var(--bg-surface)' }}>
              <Group justify="space-between" align="flex-start" wrap="nowrap" mb={4}>
                <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                  <Avatar size="sm" color={channel.avatar_color} radius="xl">{initialsOf(channel.title)}</Avatar>
                  <div style={{ minWidth: 0 }}>
                    <Group gap={4}>
                      <Text size="sm" fw={600}>{post.author_label || channel.title}</Text>
                      {post.pinned && <Pin size={12} />}
                      {post.source !== 'manual' && <Badge size="xs" variant="outline">{post.source}</Badge>}
                    </Group>
                    <Text size="xs" c="dimmed">
                      {timeAgo(post.published_at)} · {post.views.toLocaleString('ru-RU')} просмотров
                      {post.revisions_count ? ` · правок: ${post.revisions_count}` : ''}
                    </Text>
                  </div>
                </Group>
                <Menu shadow="md" position="bottom-end">
                  <Menu.Target><ActionIcon variant="subtle" size="sm"><MoreVertical size={15} /></ActionIcon></Menu.Target>
                  <Menu.Dropdown>
                    <Menu.Item leftSection={<Pencil size={14} />} onClick={() => onEditPost(post)}>Редактировать</Menu.Item>
                    <Menu.Item leftSection={<MessageSquare size={14} />} onClick={() => onOpenThread(post)}>Комментарии</Menu.Item>
                    <Menu.Item color="red" leftSection={<Trash2 size={14} />} onClick={() => onDeletePost(post)}>Удалить</Menu.Item>
                  </Menu.Dropdown>
                </Menu>
              </Group>

              <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{post.text}</Text>
              <MediaRow media={post.media} />
              <ReactionRow reactions={post.reactions} onReact={e => onReactPost(post, e)} />

              <Group mt="xs">
                <Button size="xs" variant="light" leftSection={<MessageSquare size={14} />}
                  onClick={() => onOpenThread(post)}>
                  Открыть комментарии{post.comments_count ? ` (${post.comments_count})` : ''}
                </Button>
              </Group>
            </Paper>
          ))}
        </Stack>
      </ScrollArea>
    </Box>
  );
}
