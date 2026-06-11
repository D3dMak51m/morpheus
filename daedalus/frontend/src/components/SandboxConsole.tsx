import { useEffect, useRef, useState } from 'react';
import { Terminal, Play, Smartphone, Cpu } from 'lucide-react';
import './SandboxConsole.css';

interface SandboxConsoleProps {
  token: string;
}

interface AgentOption {
  agent_id: string;
  codename: string;
  full_name: string;
}

interface DeviceOption {
  device_id: string;
  vnc_port: string | null;
  status: string | null;
}

interface LogLine {
  ts: string;
  text: string;
  kind: 'info' | 'ok' | 'fail';
}

const TARGET_APPS = ['base', 'instagram', 'telegram'];

const SandboxConsole: React.FC<SandboxConsoleProps> = ({ token }) => {
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [devices, setDevices] = useState<DeviceOption[]>([]);

  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectedDevice, setSelectedDevice] = useState('');
  const [targetApp, setTargetApp] = useState('base');
  const [payload, setPayload] = useState('');

  const [logs, setLogs] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);

  const logEndRef = useRef<HTMLDivElement>(null);

  const headers = { Authorization: `Bearer ${token}` };

  useEffect(() => {
    fetchAgents();
    fetchDevices();
    const interval = setInterval(fetchDevices, 8000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const fetchAgents = async () => {
    try {
      const res = await fetch('/api/v1/souls/profiles', { headers });
      if (res.ok) {
        const data = await res.json();
        setAgents(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Merge registered Virtual Devices with live orchestrator metadata to recover VNC ports.
  const fetchDevices = async () => {
    try {
      const [devRes, orchRes] = await Promise.all([
        fetch('/api/v1/souls/devices', { headers }),
        fetch('/api/v1/analytics/orchestrator/list', { headers }),
      ]);
      const dbDevices = devRes.ok ? await devRes.json() : [];
      const orchData = orchRes.ok ? await orchRes.json() : { emulators: [] };
      const orchEmulators: any[] = orchData.emulators || [];

      const merged: DeviceOption[] = [];
      const seen = new Set<string>();

      for (const d of dbDevices) {
        const orch = orchEmulators.find(
          e => `localhost:${e.adb_port}` === d.device_id || e.name === d.device_id
        );
        merged.push({
          device_id: d.device_id,
          vnc_port: orch?.vnc_port || null,
          status: orch?.status || d.status || null,
        });
        seen.add(d.device_id);
      }

      for (const e of orchEmulators) {
        const id = `localhost:${e.adb_port}`;
        if (!seen.has(id)) {
          merged.push({ device_id: id, vnc_port: e.vnc_port, status: e.status });
        }
      }

      setDevices(merged);
    } catch (e) {
      console.error(e);
    }
  };

  const appendLog = (text: string, kind: LogLine['kind'] = 'info') => {
    setLogs(prev => [...prev, { ts: new Date().toLocaleTimeString(), text, kind }]);
  };

  const classifyLine = (line: string): LogLine['kind'] => {
    if (line.includes('[FAIL]')) return 'fail';
    if (line.includes('[OK]')) return 'ok';
    return 'info';
  };

  const handleTrigger = async () => {
    if (!selectedAgent || !selectedDevice || !payload.trim()) {
      appendLog('[FAIL] Agent, device and a non-empty payload are required.', 'fail');
      return;
    }
    setRunning(true);
    appendLog(`▶ Dispatching to ${selectedDevice} as ${selectedAgent} [app=${targetApp}]...`, 'info');
    try {
      const res = await fetch('/api/v1/sandbox/execute', {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: selectedAgent,
          device_id: selectedDevice,
          target_app: targetApp,
          text_payload: payload,
        }),
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        appendLog(`[FAIL] HTTP ${res.status}: ${data.detail || 'Sandbox request rejected.'}`, 'fail');
      } else {
        const lines: string[] = Array.isArray(data.log) ? data.log : [];
        for (const line of lines) {
          appendLog(line, classifyLine(line));
        }
        appendLog(
          data.success ? '✔ Sandbox run reported SUCCESS.' : '✖ Sandbox run reported FAILURE.',
          data.success ? 'ok' : 'fail'
        );
      }
    } catch (e: any) {
      appendLog(`[FAIL] Network error: ${e.message}`, 'fail');
    } finally {
      setRunning(false);
    }
  };

  const activeDevice = devices.find(d => d.device_id === selectedDevice);
  const vncUrl = activeDevice?.vnc_port
    ? `http://${window.location.hostname}:${activeDevice.vnc_port}`
    : null;

  return (
    <div className="sandbox-console view-container">
      <div className="header-row">
        <div>
          <h1>Sandbox Console</h1>
          <p className="subtitle">Manually trigger isolated physical typing and watch it live — bypasses Redis queues.</p>
        </div>
      </div>

      <div className="sandbox-split">
        {/* ── Left: Controls + Log ── */}
        <div className="sandbox-controls">
          <div className="control-group">
            <label><Smartphone size={14} /> Agent</label>
            <select value={selectedAgent} onChange={e => setSelectedAgent(e.target.value)}>
              <option value="">— Select Agent —</option>
              {agents.map(a => (
                <option key={a.agent_id} value={a.agent_id}>
                  {a.full_name} ({a.agent_id})
                </option>
              ))}
            </select>
          </div>

          <div className="control-group">
            <label><Cpu size={14} /> Virtual Device</label>
            <select value={selectedDevice} onChange={e => setSelectedDevice(e.target.value)}>
              <option value="">— Select Device —</option>
              {devices.map(d => (
                <option key={d.device_id} value={d.device_id}>
                  {d.device_id}{d.vnc_port ? ` · VNC ${d.vnc_port}` : ''}{d.status ? ` · ${d.status}` : ''}
                </option>
              ))}
            </select>
          </div>

          <div className="control-group">
            <label>Target App</label>
            <div className="app-toggle">
              {TARGET_APPS.map(app => (
                <button
                  key={app}
                  className={targetApp === app ? 'active' : ''}
                  onClick={() => setTargetApp(app)}
                >
                  {app}
                </button>
              ))}
            </div>
          </div>

          <div className="control-group">
            <label>Test Payload</label>
            <textarea
              rows={4}
              value={payload}
              placeholder="Text to physically type on the device..."
              onChange={e => setPayload(e.target.value)}
            />
          </div>

          <button className="btn-trigger" onClick={handleTrigger} disabled={running}>
            <Play size={16} /> {running ? 'EXECUTING…' : 'TRIGGER EXECUTION'}
          </button>

          <div className="log-box">
            <div className="log-box-header"><Terminal size={14} /> Execution Log</div>
            <div className="log-stream">
              {logs.length === 0 && <div className="log-empty">Awaiting execution…</div>}
              {logs.map((l, i) => (
                <div key={i} className={`log-line ${l.kind}`}>
                  <span className="log-ts">{l.ts}</span> {l.text}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>

        {/* ── Right: Live VNC Monitor ── */}
        <div className="sandbox-vnc">
          <div className="vnc-header">
            Live VNC Monitor
            {activeDevice && <span className="vnc-target">{activeDevice.device_id}</span>}
          </div>
          <div className="vnc-body">
            {vncUrl ? (
              <iframe title="sandbox-vnc" src={vncUrl} className="vnc-frame" />
            ) : (
              <div className="vnc-placeholder">
                {selectedDevice
                  ? 'Selected device has no orchestrated WebVNC port available.'
                  : 'Select an orchestrated device to stream its live screen.'}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default SandboxConsole;
