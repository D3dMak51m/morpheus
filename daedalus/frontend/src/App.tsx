import { useState } from 'react';
import SoulsContext from './components/SoulsContext';
import DeviceGrid from './components/DeviceGrid';
import Login from './components/Login';
import LandscapeManager from './components/LandscapeManager';
import NewsHubInspector from './components/NewsHubInspector';
import Dashboard from './components/Dashboard';
import DatabaseExplorer from './components/DatabaseExplorer';
import ActivityStream from './components/ActivityStream';
import { Shield, HardDrive, LayoutDashboard, LogOut, Database, Activity, Map, Radio } from 'lucide-react';
import './App.css';

function App() {
  const [activeView, setActiveView] = useState<'dashboard' | 'souls' | 'devices' | 'landscape' | 'newshub' | 'database' | 'activity'>('dashboard');
  const [token, setToken] = useState<string | null>(localStorage.getItem('daedalus_token'));

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
            <LayoutDashboard size={18} /> Dashboard
          </button>
          <button
            className={`nav-item ${activeView === 'souls' ? 'active' : ''}`}
            onClick={() => setActiveView('souls')}
          >
            <Shield size={18} /> Souls
          </button>
          <button
            className={`nav-item ${activeView === 'landscape' ? 'active' : ''}`}
            onClick={() => setActiveView('landscape')}
          >
            <Map size={18} /> Landscape
          </button>
          <button
            className={`nav-item ${activeView === 'newshub' ? 'active' : ''}`}
            onClick={() => setActiveView('newshub')}
          >
            <Radio size={18} /> News Hub
          </button>
          <button
            className={`nav-item ${activeView === 'devices' ? 'active' : ''}`}
            onClick={() => setActiveView('devices')}
          >
            <HardDrive size={18} /> Devices
          </button>
          <button
            className={`nav-item ${activeView === 'activity' ? 'active' : ''}`}
            onClick={() => setActiveView('activity')}
          >
            <Activity size={18} /> Activity
          </button>
          <button
            className={`nav-item ${activeView === 'database' ? 'active' : ''}`}
            onClick={() => setActiveView('database')}
          >
            <Database size={18} /> Database
          </button>
        </div>
        <div className="sidebar-footer">
          <button className="nav-item logout" onClick={handleLogout}>
            <LogOut size={18} /> Logout
          </button>
        </div>
      </nav>

      <main className="main-content">
        {activeView === 'souls' && <SoulsContext token={token} />}
        {activeView === 'landscape' && <LandscapeManager token={token} />}
        {activeView === 'newshub' && <NewsHubInspector token={token} />}
        {activeView === 'devices' && <DeviceGrid token={token} />}
        {activeView === 'activity' && <ActivityStream token={token} />}
        {activeView === 'database' && <DatabaseExplorer token={token} />}
        {activeView === 'dashboard' && <Dashboard token={token} />}
      </main>
    </div>
  );
}

export default App;

