import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { Banner, Empty } from './common';

const TZ = ['Asia/Shanghai', 'UTC'];
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const blank = () => ({ schedule_id: '', name: '', start: '17:00', end: '00:00', timezone: 'Asia/Shanghai', days: [...DAYS], activity_count: 2, prewarm_min: 30, enabled: true });

export default function Schedules() {
  const [list, setList] = useState<any[]>([]);
  const [base, setBase] = useState(0);
  const [baseSaved, setBaseSaved] = useState(0);
  const [editing, setEditing] = useState<any | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const [sch, cfg] = await Promise.all([api.getSchedules().catch(() => ({ schedules: [] })), api.getConfig().catch(() => ({}))]);
    setList(sch.schedules || []);
    const b = Number(cfg.base_count ?? 0); setBase(b); setBaseSaved(b);
  };
  useEffect(() => { load(); }, []);

  const saveBase = async () => {
    setBusy(true); setToast(null);
    try { await api.putConfig({ base_count: Number(base) }); setBaseSaved(Number(base)); setToast({ ok: true, msg: '基础数量已保存 ✓' }); }
    catch (e: any) { setToast({ ok: false, msg: e.message }); } finally { setBusy(false); }
  };
  const saveActivity = async () => {
    if (!editing) return;
    setBusy(true); setToast(null);
    try {
      const body = { ...editing, schedule_id: editing.schedule_id || `sch-${Date.now()}`, activity_count: Number(editing.activity_count), prewarm_min: Number(editing.prewarm_min) };
      await api.putSchedule(body); setToast({ ok: true, msg: '已保存 ✓' }); setEditing(null); await load();
    } catch (e: any) { setToast({ ok: false, msg: e.message }); } finally { setBusy(false); }
  };
  const del = async (id: string) => { await api.deleteSchedule(id).catch(() => {}); await load(); };
  const toggleDay = (d: string) =>
    setEditing((s: any) => ({ ...s, days: s.days.includes(d) ? s.days.filter((x: string) => x !== d) : [...s.days, d] }));

  return (
    <>
      <Banner>总数量 = <b>基础数量</b>(常驻,窗口外维持;设 0 则夜间归零)+ <b>活动数量</b>(仅活动窗口叠加,窗口前 {editing?.prewarm_min ?? 30} 分钟预热)。时区默认上海(UTC+8)。</Banner>

      {/* 基础数量 */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3>基础数量(常驻台数)</h3>
        <div className="actions" style={{ marginTop: 4 }}>
          <div className="field" style={{ width: 160, marginBottom: 0 }}>
            <input type="number" min={0} max={8} value={base} onChange={(e) => setBase(Number(e.target.value))} />
          </div>
          <button className="btn btn-sm" onClick={saveBase} disabled={busy || base === baseSaved}>保存基础数量</button>
          <span className="total-badge">当前基础 <b>{baseSaved}</b> 台</span>
        </div>
        {baseSaved === 0 && list.length === 0 && (
          <div className="hint" style={{ color: 'var(--amber)', marginTop: 12, marginBottom: 0 }}>
            ⚠ 当前基础为 0 且无活动 —— 集群将始终保持 0 台。请设置基础数量,或在下方新建一个定时活动。
          </div>
        )}
      </div>

      {!editing && (
        <div className="actions" style={{ marginTop: 0, marginBottom: 16 }}>
          <button className="btn btn-sm" onClick={() => setEditing(blank())}>+ 新建活动</button>
          {toast && <span className={`toast ${toast.ok ? 'ok' : 'err'}`}>{toast.msg}</span>}
        </div>
      )}

      {editing ? (
        <div className="card">
          <h3>{editing.schedule_id ? '编辑活动' : '新建活动'}</h3>
          <div className="form-grid" style={{ maxWidth: 720 }}>
            <div className="field"><label>名称</label>
              <input value={editing.name || ''} placeholder="晚高峰削峰" onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></div>
            <div className="field"><label>时区</label>
              <select value={editing.timezone} onChange={(e) => setEditing({ ...editing, timezone: e.target.value })}>
                {TZ.map((t) => <option key={t} value={t}>{t === 'Asia/Shanghai' ? 'Asia/Shanghai (UTC+8)' : t}</option>)}</select></div>
            <div className="field"><label>开始时间</label>
              <input type="time" value={editing.start} onChange={(e) => setEditing({ ...editing, start: e.target.value })} /></div>
            <div className="field"><label>结束时间(次日 0 点填 00:00)</label>
              <input type="time" value={editing.end === '24:00' ? '00:00' : editing.end} onChange={(e) => setEditing({ ...editing, end: e.target.value })} /></div>
            <div className="field"><label>活动数量(叠加在基础之上)</label>
              <input type="number" min={0} max={8} value={editing.activity_count} onChange={(e) => setEditing({ ...editing, activity_count: e.target.value })} /></div>
            <div className="field"><label>预热提前量(分钟)</label>
              <input type="number" min={0} max={90} value={editing.prewarm_min} onChange={(e) => setEditing({ ...editing, prewarm_min: e.target.value })} /></div>
          </div>
          <div className="field" style={{ marginTop: 6 }}><label>生效日</label>
            <div className="subnav">
              {DAYS.map((d) => (
                <button key={d} className={editing.days.includes(d) ? 'on' : ''} onClick={() => toggleDay(d)}>{d}</button>
              ))}
            </div>
          </div>
          <div className="actions" style={{ marginTop: 0, marginBottom: 8 }}>
            <span className="total-badge">窗口内总数量 = 基础 {baseSaved} + 活动 {Number(editing.activity_count) || 0} = <b>{baseSaved + (Number(editing.activity_count) || 0)}</b> 台</span>
          </div>
          <label className="chk"><input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /> 启用</label>
          <div className="actions">
            <button className="btn btn-sm" onClick={saveActivity} disabled={busy}>{busy ? '保存中…' : '保存'}</button>
            <button className="btn btn-sm btn-ghost" onClick={() => setEditing(null)}>取消</button>
            {toast && <span className={`toast ${toast.ok ? 'ok' : 'err'}`}>{toast.msg}</span>}
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <table>
            <thead><tr><th>名称</th><th>窗口</th><th>时区</th><th>生效日</th><th>活动数量</th><th>窗口内总数</th><th>状态</th><th></th></tr></thead>
            <tbody>
              {list.length === 0 ? (
                <tr><td colSpan={8}><Empty>暂无活动 —— 点击「新建活动」添加窗口</Empty></td></tr>
              ) : list.map((s) => {
                const ac = Number(s.activity_count ?? s.target ?? 0);
                return (
                  <tr key={s.schedule_id}>
                    <td>{s.name || s.schedule_id}</td>
                    <td className="b-teal">{s.start}–{s.end}</td>
                    <td className="faint">{s.timezone}</td>
                    <td className="faint" style={{ fontSize: 12 }}>{(s.days || []).length === 7 ? '每天' : (s.days || []).join(' ')}</td>
                    <td>+{ac}</td>
                    <td className="b-blue">{baseSaved + ac}</td>
                    <td>{s.enabled ? <span className="tag g">启用</span> : <span className="tag">停用</span>}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button className="del" onClick={() => setEditing({ ...blank(), ...s, activity_count: ac })}>编辑</button>
                      <button className="del" onClick={() => del(s.schedule_id)}>删除</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
