import { useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import ErrorBoundary from './ErrorBoundary';
import Overview from './Overview';
import Fleet from './Fleet';
import Monitoring from './Monitoring';
import Regions from './Regions';
import GlobalAccelerator from './GlobalAccelerator';
import EnvWizard from './EnvWizard';
import Schedules from './Schedules';
import AgentLog from './AgentLog';

export type View = 'overview' | 'fleet' | 'monitoring' | 'regions' | 'ga' | 'env' | 'schedules' | 'agent';
export type OnNavigate = (v: View) => void;
type NavItem = { k: View; ico: string; label: string };
const NAV_GROUPS: { title?: string; items: NavItem[] }[] = [
  { items: [{ k: 'overview', ico: '◈', label: '概览' }] },
  {
    title: '监控运维', items: [
      { k: 'fleet', ico: '▤', label: '实例队列' },
      { k: 'monitoring', ico: '◔', label: '性能监控' },
      { k: 'regions', ico: '◱', label: '区域容量' },
      { k: 'ga', ico: '◎', label: 'Global Accelerator' },
      { k: 'agent', ico: '✦', label: 'Agent 控制 / 审计' },
    ],
  },
  {
    title: '配置', items: [
      { k: 'env', ico: '⬡', label: '环境配置向导' },
      { k: 'schedules', ico: '◷', label: '定时活动' },
    ],
  },
];
const NAV: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

export default function Dashboard() {
  const { user, logout } = useAuth();
  const [view, setView] = useState<View>('overview');

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="logo">N</div>
          <div><h1 style={{ fontSize: 16 }}>NLP-Platform</h1><div className="sub">Control Plane</div></div>
        </div>
        <nav className="nav">
          {NAV_GROUPS.map((g, gi) => (
            <div className="nav-sec" key={gi} role="group" aria-label={g.title || '概览'}>
              {g.title && <div className="nav-group">{g.title}</div>}
              {g.items.map((n) => (
                <button key={n.k} className={view === n.k ? 'on' : ''} aria-current={view === n.k ? 'page' : undefined} onClick={() => setView(n.k)}>
                  <span className="ico" aria-hidden="true">{n.ico}</span>{n.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="foot">us-east-1 · dev<br />via Global Accelerator</div>
      </aside>

      <main className="main">
        <div className="topbar">
          <h2>{NAV.find((n) => n.k === view)?.label}</h2>
          <div className="who">
            <span className="pill on"><span className="dot g" style={{ marginRight: 6 }} />在线</span>
            <div className="avatar">{(user?.username || 'U')[0].toUpperCase()}</div>
            <span>{user?.username}</span>
            <button className="logout" onClick={logout}>登出</button>
          </div>
        </div>

        <ErrorBoundary resetKey={view}>
          {view === 'overview' && <Overview onNavigate={setView} />}
          {view === 'fleet' && <Fleet />}
          {view === 'monitoring' && <Monitoring />}
          {view === 'regions' && <Regions />}
          {view === 'ga' && <GlobalAccelerator />}
          {view === 'env' && <EnvWizard />}
          {view === 'schedules' && <Schedules />}
          {view === 'agent' && <AgentLog />}
        </ErrorBoundary>
      </main>
    </div>
  );
}
