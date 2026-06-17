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
import { Shield, HardDrive, LayoutDashboard, LogOut, Database, Activity, Map, Radio, Key, Dna, Users, TerminalSquare, Target, Radar, Brain, Factory, Compass, ListChecks } from 'lucide-react';
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
    <div className="app-shell">
      <nav className="sidebar">
        <div className="sidebar-header">
          <h2>DAEDALUS</h2>
          <span className="subtitle">MORPHEUS CONTROL</span>
        </div>
        <div className="sidebar-nav">
          <button
            className={`nav-item ${activeView === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveView('dashboard')}
          >
            <LayoutDashboard size={18} /> Дашборд
          </button>
          <button
            className={`nav-item ${activeView === 'live' ? 'active' : ''}`}
            onClick={() => setActiveView('live')}
          >
            <Activity size={18} /> Лента событий
          </button>
          <button
            className={`nav-item ${activeView === 'swarm' ? 'active' : ''}`}
            onClick={() => setActiveView('swarm')}
          >
            <Users size={18} /> Рой
          </button>

          <div className="sidebar-group-label">ПЕРСОНЫ</div>
          <button
            className={`nav-item ${activeView === 'accounts' ? 'active' : ''}`}
            onClick={() => setActiveView('accounts')}
          >
            <Users size={18} /> Аккаунты
          </button>
          <button
            className={`nav-item ${activeView === 'souls' ? 'active' : ''}`}
            onClick={() => setActiveView('souls')}
          >
            <Shield size={18} /> Души (хранилище)
          </button>
          <button
            className={`nav-item ${activeView === 'genesis' ? 'active' : ''}`}
            onClick={() => setActiveView('genesis')}
          >
            <Dna size={18} /> Генезис душ
          </button>
          <button
            className={`nav-item ${activeView === 'factory' ? 'active' : ''}`}
            onClick={() => setActiveView('factory')}
          >
            <Factory size={18} /> Фабрика клонов
          </button>
          <button
            className={`nav-item ${activeView === 'auth' ? 'active' : ''}`}
            onClick={() => setActiveView('auth')}
          >
            <Key size={18} /> Фабрика авторизации
          </button>

          <div className="sidebar-group-label">СБОР</div>
          <button
            className={`nav-item ${activeView === 'landscape' ? 'active' : ''}`}
            onClick={() => setActiveView('landscape')}
          >
            <Map size={18} /> Ландшафт
          </button>
          <button
            className={`nav-item ${activeView === 'newshub' ? 'active' : ''}`}
            onClick={() => setActiveView('newshub')}
          >
            <Radio size={18} /> Центр новостей
          </button>
          <button
            className={`nav-item ${activeView === 'muninn' ? 'active' : ''}`}
            onClick={() => setActiveView('muninn')}
          >
            <Brain size={18} /> Знания роя
          </button>
          <button
            className={`nav-item ${activeView === 'channelprofiles' ? 'active' : ''}`}
            onClick={() => setActiveView('channelprofiles')}
          >
            <Compass size={18} /> Профили каналов
          </button>

          <div className="sidebar-group-label">ИСПОЛНЕНИЕ</div>
          <button
            className={`nav-item ${activeView === 'scouting' ? 'active' : ''}`}
            onClick={() => setActiveView('scouting')}
          >
            <Radar size={18} /> Радар разведки
          </button>
          <button
            className={`nav-item ${activeView === 'missions' ? 'active' : ''}`}
            onClick={() => setActiveView('missions')}
          >
            <Target size={18} /> Миссии
          </button>
          <button
            className={`nav-item ${activeView === 'devices' ? 'active' : ''}`}
            onClick={() => setActiveView('devices')}
          >
            <HardDrive size={18} /> Устройства
          </button>
          <button
            className={`nav-item ${activeView === 'sandbox' ? 'active' : ''}`}
            onClick={() => setActiveView('sandbox')}
          >
            <TerminalSquare size={18} /> Песочница
          </button>
          <button
            className={`nav-item ${activeView === 'decisions' ? 'active' : ''}`}
            onClick={() => setActiveView('decisions')}
          >
            <ListChecks size={18} /> Решения
          </button>
          <button
            className={`nav-item ${activeView === 'activity' ? 'active' : ''}`}
            onClick={() => setActiveView('activity')}
          >
            <Activity size={18} /> Журнал (лог)
          </button>
          
          <div className="sidebar-group-label">СИСТЕМА</div>
          <button
            className={`nav-item ${activeView === 'database' ? 'active' : ''}`}
            onClick={() => setActiveView('database')}
          >
            <Database size={18} /> База данных
          </button>
        </div>
        <div className="sidebar-footer">
          <button className="nav-item logout" onClick={handleLogout}>
            <LogOut size={18} /> Выход
          </button>
        </div>
      </nav>

      <main className="main-content">
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
      </main>
    </div>
  );
}

export default App;

