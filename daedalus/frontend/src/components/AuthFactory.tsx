import { useState, useEffect } from 'react';
import {
  Box, Group, Stack, Title, Text, Button, Tabs, TextInput, PasswordInput, Select, Alert, Stepper, Paper, Badge,
} from '@mantine/core';
import { Key, Send, ShieldCheck, CheckCircle2, Smartphone } from 'lucide-react';

interface Soul { agent_id: string; full_name: string; codename: string; }
interface Device { id: number; device_id: string; }

export function AuthFactory({ token }: { token: string | null }) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;

  const [agentId, setAgentId] = useState<string | null>(null);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [souls, setSouls] = useState<Soul[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);

  const [phone, setPhone] = useState('');
  const [hash, setHash] = useState('');
  const [otp, setOtp] = useState('');
  const [twoFa, setTwoFa] = useState('');
  const [tgStep, setTgStep] = useState<1 | 2 | 3>(1);
  const [tgLoading, setTgLoading] = useState(false);

  const [mobilePlatform, setMobilePlatform] = useState('instagram');
  const [mobileUsername, setMobileUsername] = useState('');
  const [mobileLoading, setMobileLoading] = useState(false);

  const [msg, setMsg] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  useEffect(() => {
    (async () => {
      const sr = await fetch('/api/v1/souls/profiles', { headers }); if (sr.ok) setSouls(await sr.json());
      const dr = await fetch('/api/v1/souls/devices', { headers }); if (dr.ok) setDevices(await dr.json());
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const requestCode = async () => {
    setTgLoading(true); setMsg(null);
    try {
      const r = await fetch('/api/v1/auth-factory/telegram/request-code', { method: 'POST', headers, body: JSON.stringify({ phone_number: phone }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.message || 'Не удалось запросить код');
      setHash(d.phone_code_hash || ''); setTgStep(2); setMsg({ text: d.message || 'Код отправлен', type: 'success' });
    } catch (e: any) { setMsg({ text: e.message, type: 'error' }); }
    finally { setTgLoading(false); }
  };
  const verifyCode = async () => {
    setTgLoading(true); setMsg(null);
    try {
      const payload: any = { phone_code_hash: hash, phone_number: phone, code: otp, agent_id: agentId, device_id: deviceId };
      if (twoFa) payload.password = twoFa;
      const r = await fetch('/api/v1/auth-factory/telegram/verify-code', { method: 'POST', headers, body: JSON.stringify(payload) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.message || 'Не удалось подтвердить код');
      setTgStep(3); setMsg({ text: d.message || 'Аккаунт авторизован', type: 'success' });
    } catch (e: any) { setMsg({ text: e.message, type: 'error' }); }
    finally { setTgLoading(false); }
  };
  const autoExtract = async () => {
    if (!deviceId || !mobileUsername) { setMsg({ text: 'Укажите устройство и username.', type: 'error' }); return; }
    setMobileLoading(true); setMsg(null);
    try {
      const r = await fetch('/api/v1/auth-factory/mobile/extract-session', { method: 'POST', headers, body: JSON.stringify({ platform: mobilePlatform, device_id: deviceId, username: mobileUsername, agent_id: agentId || null }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.message || 'Извлечение не удалось');
      setMsg({ text: d.message, type: 'success' });
    } catch (e: any) { setMsg({ text: e.message, type: 'error' }); }
    finally { setMobileLoading(false); }
  };

  const reset = () => { setTgStep(1); setPhone(''); setOtp(''); setTwoFa(''); setHash(''); setMsg(null); };

  const soulData = souls.map(s => ({ value: s.agent_id, label: `${s.full_name || s.codename} (${s.agent_id})` }));
  const deviceData = devices.map(d => ({ value: d.device_id, label: d.device_id }));

  return (
    <Box p="lg">
      <Group justify="space-between" mb="md">
        <div>
          <Title order={2}><Key size={22} style={{ verticalAlign: -4 }} /> Фабрика авторизации</Title>
          <Text size="sm" c="dimmed">Подключение реального аккаунта в рой: вход в Telegram (MTProto) или импорт мобильной сессии.</Text>
        </div>
      </Group>

      <Paper withBorder p="lg" radius="md" maw={860}>
        <Group grow mb="lg">
          <Select label="Привязать к душе (необязательно)" placeholder="оставьте пустым для свободного аккаунта"
            searchable clearable data={soulData} value={agentId} onChange={setAgentId} />
          <Select label="Устройство (необязательно)" placeholder="выберите устройство"
            searchable clearable data={deviceData} value={deviceId} onChange={setDeviceId} />
        </Group>

        {msg && <Alert color={msg.type === 'success' ? 'teal' : 'red'} variant="light" mb="lg">{msg.text}</Alert>}

        <Tabs defaultValue="telegram">
          <Tabs.List mb="lg">
            <Tabs.Tab value="telegram" leftSection={<Send size={14} />}>Telegram (Pyrogram)</Tabs.Tab>
            <Tabs.Tab value="mobile" leftSection={<Smartphone size={14} />}>Импорт мобильной сессии</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="telegram">
            <Stepper active={tgStep - 1} size="sm" mb="lg">
              <Stepper.Step label="Запрос кода" icon={<Send size={16} />} />
              <Stepper.Step label="Подтверждение" icon={<ShieldCheck size={16} />} />
              <Stepper.Step label="Готово" icon={<CheckCircle2 size={16} />} />
            </Stepper>

            {tgStep === 1 && (
              <Group align="flex-end">
                <TextInput style={{ flex: 1 }} label="Номер телефона" value={phone} onChange={e => setPhone(e.currentTarget.value)} placeholder="+998901234567" />
                <Button leftSection={<Send size={15} />} loading={tgLoading} disabled={!phone} onClick={requestCode}>Отправить код</Button>
              </Group>
            )}
            {tgStep === 2 && (
              <Stack gap="md">
                <Group grow>
                  <TextInput label="Код из Telegram" value={otp} onChange={e => setOtp(e.currentTarget.value)} placeholder="12345" />
                  <PasswordInput label="2FA пароль (если есть)" value={twoFa} onChange={e => setTwoFa(e.currentTarget.value)} placeholder="необязательно" />
                </Group>
                <Group><Button variant="default" onClick={reset}>Назад</Button><Button leftSection={<ShieldCheck size={15} />} loading={tgLoading} disabled={!otp} onClick={verifyCode}>Авторизовать и сохранить</Button></Group>
              </Stack>
            )}
            {tgStep === 3 && (
              <Alert color="teal" variant="light" icon={<CheckCircle2 size={18} />} title="Авторизация завершена">
                <Text size="sm">Сессия безопасно сохранена в базу{agentId ? <> и привязана к <Badge variant="light">{agentId}</Badge></> : ' (свободный аккаунт)'}.</Text>
                <Button variant="subtle" size="xs" mt="sm" onClick={reset}>Авторизовать ещё один аккаунт</Button>
              </Alert>
            )}
          </Tabs.Panel>

          <Tabs.Panel value="mobile">
            <Stack gap="md">
              <Group grow>
                <Select label="Платформа" data={[{ value: 'instagram', label: 'Instagram' }, { value: 'twitter', label: 'X (Twitter)' }, { value: 'threads', label: 'Threads' }, { value: 'youtube', label: 'YouTube' }]} value={mobilePlatform} onChange={v => v && setMobilePlatform(v)} />
                <TextInput label="Username" value={mobileUsername} onChange={e => setMobileUsername(e.currentTarget.value)} placeholder="@username" />
              </Group>
              <Alert color="indigo" variant="light" title="Автономное извлечение сессии">
                <Text size="sm">MYRMIDON управляет эмулятором ({deviceId || 'устройство'}) и выгружает живую сессию. Убедитесь, что приложение {mobilePlatform} залогинено и активно. (Мобильный стек вне scope — может не работать.)</Text>
              </Alert>
              <Button leftSection={<Smartphone size={15} />} loading={mobileLoading} onClick={autoExtract}>Извлечь сессию из эмулятора</Button>
            </Stack>
          </Tabs.Panel>
        </Tabs>
      </Paper>
    </Box>
  );
}
