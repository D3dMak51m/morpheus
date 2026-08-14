import { useState } from 'react';
import { Center, Paper, Stack, Title, Text, TextInput, PasswordInput, Button, Alert } from '@mantine/core';

interface LoginProps { onLogin: (token: string) => void; }

const Login = ({ onLogin }: LoginProps) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(''); setLoading(true);
    try {
      const body = new URLSearchParams({ username, password }).toString();
      const r = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || 'Ошибка авторизации'); }
      const d = await r.json();
      onLogin(d.access_token);
    } catch (err: any) { setError(err.message || 'Неизвестная ошибка'); }
    finally { setLoading(false); }
  };

  return (
    <Center h="100vh" style={{ background: 'var(--bg-base)' }}>
      <Paper withBorder radius="lg" p="xl" w={400} style={{ background: 'var(--bg-surface)' }}>
        <Stack gap="xs" align="center" mb="lg">
          <Title order={1} style={{ letterSpacing: 4 }}>DAEDALUS</Title>
          <Text size="sm" c="indigo" fw={700}>MORPHEUS CONTROL PANEL</Text>
        </Stack>
        <form onSubmit={submit}>
          <Stack gap="md">
            <TextInput label="Имя пользователя" value={username} onChange={e => setUsername(e.currentTarget.value)} autoFocus required />
            <PasswordInput label="Пароль" value={password} onChange={e => setPassword(e.currentTarget.value)} required />
            {error && <Alert color="red" variant="light">{error}</Alert>}
            <Button type="submit" fullWidth loading={loading} mt="xs">Войти</Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
};

export default Login;
