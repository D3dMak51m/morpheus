import { useState, useEffect } from 'react';
import SoulsContext from './components/SoulsContext';
import DeviceGrid from './components/DeviceGrid';
import Login from './components/Login';
import LandscapeManager from './components/LandscapeManager';
import NewsHubInspector from './components/NewsHubInspector';
import Dashboard from './components/Dashboard';
import DatabaseExplorer from './components/DatabaseExplorer';
import ActivityStream from './components/ActivityStream';
import LiveOps from './components/LiveOps';
import SwarmDashboard from './components/SwarmDashboard';
import { AuthFactory } from './components/AuthFactory';
import { SoulGenesisView } from './components/SoulGenesisView';
import AccountsManager from './components/AccountsManager';
import SandboxConsole from './components/SandboxConsole';
import MissionDeck from './components/MissionDeck';
import ScoutingRadar, { MissionPrefill } from './components/ScoutingRadar';
import MuninnExplorer from './components/MuninnExplorer';
import ChannelProfiles from './components/ChannelProfiles';
import DecisionLog from './components/DecisionLog';
import CloneFactory from './components/CloneFactory';
import { AppShell, ScrollArea } from '@mantine/core';
import { Shield, HardDrive, LayoutDashboard, LogOut, Database, Activity, Map, Radio, Key, Dna, Users, TerminalSquare, Target, Radar, Brain, Factory, Compass, ListChecks, type LucideIcon } from 'lucide-react';
import './App.css';

// Views are addressable via the URL hash (#/swarm, #/missions, …) so a page refresh
// keeps you where you were, links are shareable, and browser back/forward work — instead
// of always snapping back to Dashboard.
const VIEWS = ['dashboard', 'live', 'swarm', 'accounts', 'souls', 'genesis', 'factory', 'auth',
  'devices', 'sandbox', 'missions', 'scouting', 'landscape', 'newshub', 'muninn',
  'channelprofiles', 'decisions', 'database', 'activity'] as const;
type View = typeof VIEWS[number];

function readHashView(): View {
  const h = window.location.hash.replace(/^#\/?/, '');
  return (VIEWS as readonly string[]).includes(h) ? (h as View) : 'dashboard';
}

// Data-driven sidebar — grouped nav items (label '' = the top, ungrouped block).
interface NavItem { view: View; label: string; Icon: LucideIcon; }
const NAV: { label: string; items: NavItem[] }[] = [
  { label: '', items: [
    { view: 'dashboard', label: 'Дашборд', Icon: LayoutDashboard },
    { view: 'live', label: 'Лента событий', Icon: Activity },
    { view: 'swarm', label: 'Рой', Icon: Users },
  ] },
  { label: 'ПЕРСОНЫ', items: [
    { view: 'accounts', label: 'Аккаунты', Icon: Users },
    { view: 'souls', label: 'Души (хранилище)', Icon: Shield },
    { view: 'genesis', label: 'Генезис душ', Icon: Dna },
    { view: 'factory', label: 'Фабрика клонов', Icon: Factory },
    { view: 'auth', label: 'Фабрика авторизации', Icon: Key },
  ] },
  { label: 'СБОР', items: [
    { view: 'landscape', label: 'Ландшафт', Icon: Map },
    { view: 'newshub', label: 'Центр новостей', Icon: Radio },
    { view: 'muninn', label: 'Знания роя', Icon: Brain },
    { view: 'channelprofiles', label: 'Профили каналов', Icon: Compass },
  ] },
  { label: 'ИСПОЛНЕНИЕ', items: [
    { view: 'scouting', label: 'Радар разведки', Icon: Radar },
    { view: 'missions', label: 'Миссии', Icon: Target },
    { view: 'devices', label: 'Устройства', Icon: HardDrive },
    { view: 'sandbox', label: 'Песочница', Icon: TerminalSquare },
    { view: 'decisions', label: 'Решения', Icon: ListChecks },
    { view: 'activity', label: 'Журнал (лог)', Icon: Activity },
  ] },
  { label: 'СИСТЕМА', items: [
    { view: 'database', label: 'База данных', Icon: Database },
  ] },
];

function useHashView(): [View, (v: View) => void] {
  const [view, setView] = useState<View>(readHashView);
  useEffect(() => {
    const onHash = () => setView(readHashView());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const navigate = (v: View) => { window.location.hash = '/' + v; };
  return [view, navigate];
}

function App() {
  const [activeView, setActiveView] = useHashView();
  const [token, setToken] = useState<string | null>(localStorage.getItem('daedalus_token'));
  const [missionPrefill, setMissionPrefill] = useState<MissionPrefill | null>(null);

  const handleConverted = (prefill: MissionPrefill) => {
    setMissionPrefill(prefill);
    setActiveView('missions');
  };

  const handleLogin = (jwt: string) => {
    localStorage.setItem('daedalus_token', jwt);
    setToken(jwt);
  };

  const handleLogout = () => {
    localStorage.removeItem('daedalus_token');
    setToken(null);
  };

  if (!token) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <AppShell navbar={{ width: 248, breakpoint: 0 }} padding={0}>
      <AppShell.Navbar style={{ background: 'var(--bg-surface)', borderRight: '1px solid var(--border-subtle)' }}>
        <div className="sidebar-inner">
          <div className="sidebar-header">
            <h2>DAEDALUS</h2>
            <span className="subtitle">MORPHEUS CONTROL</span>
          </div>
          <ScrollArea style={{ flex: 1 }} type="hover" scrollbarSize={8}>
            <div className="sidebar-nav">
              {NAV.map(group => (
                <div key={group.label || 'top'} className="nav-group">
                  {group.label && <div className="sidebar-group-label">{group.label}</div>}
                  {group.items.map(({ view, label, Icon }) => (
                    <button
                      key={view}
                      className={`nav-item ${activeView === view ? 'active' : ''}`}
                      onClick={() => setActiveView(view)}
                    >
                      <Icon size={18} /> {label}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </ScrollArea>
          <div className="sidebar-footer">
            <button className="nav-item logout" onClick={handleLogout}>
              <LogOut size={18} /> Выход
            </button>
          </div>
        </div>
      </AppShell.Navbar>

      <AppShell.Main style={{ height: '100vh', overflowY: 'auto', background: 'var(--bg-base)' }}>
        <div style={{ display: activeView === 'accounts' ? 'block' : 'none', height: '100%' }}><AccountsManager token={token} /></div>
        <div style={{ display: activeView === 'souls' ? 'block' : 'none', height: '100%' }}><SoulsContext token={token} /></div>
        <div style={{ display: activeView === 'genesis' ? 'block' : 'none', height: '100%' }}><SoulGenesisView /></div>
        <div style={{ display: activeView === 'factory' ? 'block' : 'none', height: '100%' }}><CloneFactory token={token} /></div>
        <div style={{ display: activeView === 'auth' ? 'block' : 'none', height: '100%' }}><AuthFactory token={token} /></div>
        <div style={{ display: activeView === 'landscape' ? 'block' : 'none', height: '100%' }}><LandscapeManager token={token} /></div>
        <div style={{ display: activeView === 'newshub' ? 'block' : 'none', height: '100%' }}><NewsHubInspector token={token} /></div>
        <div style={{ display: activeView === 'muninn' ? 'block' : 'none', height: '100%' }}><MuninnExplorer token={token} /></div>
        <div style={{ display: activeView === 'channelprofiles' ? 'block' : 'none', height: '100%' }}><ChannelProfiles token={token} /></div>
        <div style={{ display: activeView === 'decisions' ? 'block' : 'none', height: '100%' }}><DecisionLog token={token} /></div>
        <div style={{ display: activeView === 'devices' ? 'block' : 'none', height: '100%' }}><DeviceGrid token={token} /></div>
        <div style={{ display: activeView === 'sandbox' ? 'block' : 'none', height: '100%' }}><SandboxConsole token={token} /></div>
        <div style={{ display: activeView === 'scouting' ? 'block' : 'none', height: '100%' }}><ScoutingRadar token={token} onConverted={handleConverted} /></div>
        <div style={{ display: activeView === 'missions' ? 'block' : 'none', height: '100%' }}><MissionDeck token={token} prefill={missionPrefill} onPrefillConsumed={() => setMissionPrefill(null)} /></div>
        <div style={{ display: activeView === 'activity' ? 'block' : 'none', height: '100%' }}><ActivityStream token={token} /></div>
        <div style={{ display: activeView === 'database' ? 'block' : 'none', height: '100%' }}><DatabaseExplorer token={token} /></div>
        <div style={{ display: activeView === 'dashboard' ? 'block' : 'none', height: '100%' }}><Dashboard token={token} /></div>
        <div style={{ display: activeView === 'live' ? 'block' : 'none', height: '100%' }}><LiveOps token={token} /></div>
        <div style={{ display: activeView === 'swarm' ? 'block' : 'none', height: '100%' }}><SwarmDashboard token={token} onNavigate={(v) => setActiveView(v as typeof activeView)} /></div>
      </AppShell.Main>
    </AppShell>
  );
}

export default App;

