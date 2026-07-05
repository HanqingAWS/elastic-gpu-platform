import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { useLive, Loading, Empty, Banner, REGION_LABEL } from './common';

const ACTION_LABEL: Record<string, string> = {
  set_asg_desired: '调整 ASG 台数',
  trigger_od_backfill: 'OD 兜底补容',
  set_ga_weights: '调整 GA 权重',
  rebalance_regions: '跨区再平衡',
};
const KIND_CLS: Record<string, string> = { spot: 'b', od: 'a', ga: 'v' };
const MODELS = [
  { id: 'global.anthropic.claude-sonnet-5', label: 'Sonnet 5 · 最新' },
  { id: 'global.anthropic.claude-sonnet-4-6', label: 'Sonnet 4.6 · 均衡(默认)' },
  { id: 'global.anthropic.claude-opus-4-8', label: 'Opus 4.8 · 最强' },
  { id: 'global.anthropic.claude-haiku-4-5-20251001', label: 'Haiku 4.5 · 最省' },
];
const DEFAULT_MODEL = 'global.anthropic.claude-sonnet-4-6';

function tsOf(a: any): number {
  if (a.ts) return Number(a.ts) * 1000;
  const p = String(a.ts_uuid || '').split('#')[0];
  return p ? Number(p) : 0;
}
function change(a: any): string {
  const u = a.unit || '';
  const hasA = a.after !== undefined && a.after !== null;
  const hasB = a.before !== undefined && a.before !== null;
  if (hasB && hasA) return `${a.before} → ${a.after}${u}`;
  if (hasA) return `→ ${a.after}${u}`;
  return '—';
}

// ---- Agent 运行控制(运行模式 / 启用暂停),写 Config,Agent 每 tick 读取 ----
function AgentControl() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [modelId, setModelId] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [testRes, setTestRes] = useState<any>(null);

  const load = async () => {
    const c = await api.getConfig().catch(() => ({}));
    setEnabled(c.agent_enabled !== false); // 默认 true
    setModelId(c.agent_model_id || DEFAULT_MODEL);
  };
  useEffect(() => { load(); }, []);

  const save = async (patch: any, msg: string) => {
    setBusy(true); setToast(null);
    try { await api.putConfig(patch); setToast({ ok: true, msg: `${msg} ✓ (Agent 约 45s 内生效)` }); await load(); }
    catch (e: any) { setToast({ ok: false, msg: e.message }); }
    finally { setBusy(false); }
  };
  const test = async () => {
    setTesting(true); setTestRes(null);
    try { setTestRes(await api.testModel(modelId.trim())); }
    catch (e: any) { setTestRes({ ok: false, error: e.message }); }
    finally { setTesting(false); }
  };
  if (enabled === null) return null;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h3>Agent 运行控制</h3>
      <div className="rowflex" style={{ gap: 24 }}>
        <div style={{ flex: 'none' }}>
          <div className="hint" style={{ margin: '0 0 6px' }}>调度状态</div>
          <div className="subnav" style={{ margin: 0 }}>
            <button className={enabled ? 'on' : ''} onClick={() => enabled ? null : save({ agent_enabled: true }, '已启用')} disabled={busy}>启用</button>
            <button className={!enabled ? 'on' : ''} onClick={() => !enabled ? null : save({ agent_enabled: false }, '已暂停')} disabled={busy}>暂停(只观测)</button>
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 200, alignSelf: 'center' }}>
          <span className={`tag ${enabled ? 'g' : 'a'}`}>{enabled ? '运行中' : '已暂停'}</span>
          {toast && <div className={`toast ${toast.ok ? 'ok' : 'err'}`} style={{ marginLeft: 10, display: 'inline-block' }}>{toast.msg}</div>}
        </div>
      </div>
      <div style={{ marginTop: 16 }}>
        <div className="hint" style={{ margin: '0 0 6px' }}>决策模型(Bedrock · 仅边缘态调用)</div>
        <div className="actions" style={{ marginTop: 0 }}>
          <div className="field" style={{ flex: 1, maxWidth: 420, marginBottom: 0 }}>
            <select value={modelId} onChange={(e) => { setModelId(e.target.value); setTestRes(null); }}>
              {modelId && !MODELS.some((m) => m.id === modelId) && <option value={modelId}>{modelId}(自定义)</option>}
              {MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={test} disabled={busy || testing || !modelId.trim()}>{testing ? '测试中…' : '测试'}</button>
          <button className="btn btn-sm" onClick={() => save({ agent_model_id: modelId.trim() }, '已更新决策模型')} disabled={busy || !modelId.trim()}>保存模型</button>
        </div>
        {testRes && (
          <div className={`toast ${testRes.ok ? 'ok' : 'err'}`} style={{ marginTop: 8, display: 'inline-block' }}>
            {testRes.ok ? `✓ 可用 · ${testRes.latency_ms}ms${testRes.sample ? ` · 回复"${testRes.sample}"` : ''}` : `✗ ${testRes.error}`}
          </div>
        )}
        <div className="mono faint" style={{ fontSize: 12, marginTop: 6 }}>模型 ID:{modelId}</div>
      </div>
      <p className="hint" style={{ marginTop: 12, marginBottom: 0 }}>
        启用:Agent 按排期 / 基础台数自动调度真实资源(受护栏:clamp≤8、冷却、每-tick 上限)。暂停:只观测、不做任何变更。改动经 Config 表,Agent 每 tick(~45s)自动读取,无需重部署。
      </p>
    </div>
  );
}

// UTC+8(Asia/Shanghai)日期 —— 与页面显示的本地时间一致
const sgDay = (offset = 0) => new Date(Date.now() + offset * 86400000 + 8 * 3600000).toISOString().slice(0, 10);

function ActionsTable() {
  const [date, setDate] = useState('');  // '' = 近期(scan);否则按 UTC+8 天筛
  const { data, loading } = useLive(() => api.agentActions(date || undefined), 15000, date);
  const actions: any[] = data?.actions ?? [];
  return (
    <>
      <Banner>Agent / 规则的每次<b>真实变更</b>都经护栏(clamp≤8 / 冷却 / 每-tick 上限)并落审计。每条记录含:来源、动作、哪个区哪种 ASG、从多少到多少、原因。暂停期间不产生任何变更、也不记录。</Banner>
      <div className="subnav" style={{ alignItems: 'center' }}>
        <button className={!date ? 'on' : ''} onClick={() => setDate('')}>近期</button>
        <button className={date === sgDay(0) ? 'on' : ''} onClick={() => setDate(sgDay(0))}>今天</button>
        <button className={date === sgDay(-1) ? 'on' : ''} onClick={() => setDate(sgDay(-1))}>昨天</button>
        <input type="date" value={date} max={sgDay(0)} onChange={(e) => setDate(e.target.value)} style={{ width: 'auto' }} />
        <span className="faint" style={{ fontSize: 12 }}>按 UTC+8 日筛选{loading ? ' · 加载中…' : ''}</span>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table>
          <thead><tr>
            <th>时间</th><th>来源</th><th>动作</th><th>区域 / 类型</th><th>变更(前 → 后)</th><th>原因</th>
          </tr></thead>
          <tbody>
            {actions.length === 0 ? (
              <tr><td colSpan={6}><Empty icon="✦" hint="启用后,Agent 在活动窗口按排期真实调度时,每次变更在此结构化留痕。">暂无决策记录</Empty></td></tr>
            ) : actions.map((a, i) => {
              const t = tsOf(a);
              const kind = a.kind || '';
              return (
                <tr key={a.ts_uuid || i}>
                  <td className="faint">{t ? new Date(t).toLocaleString() : (a.date || '—')}</td>
                  <td><span className={`tag ${a.source === 'agent' ? 'v' : 'b'}`}>{a.source === 'agent' ? 'Agent' : '规则'}</span></td>
                  <td>{ACTION_LABEL[a.action] || a.action || '—'}</td>
                  <td>
                    {a.region && a.region !== '-' ? <span>{a.region}</span> : ''}
                    {kind ? <span className={`tag ${KIND_CLS[kind] || ''}`} style={{ marginLeft: 6 }}>{kind.toUpperCase()}</span> : ''}
                    {(!a.region || a.region === '-') && !kind ? '—' : ''}
                    {a.region && REGION_LABEL[a.region] ? <div className="faint" style={{ fontSize: 11 }}>{REGION_LABEL[a.region]}</div> : null}
                  </td>
                  <td className="mono b-teal">{change(a)}</td>
                  <td className="faint" style={{ fontSize: 12.5 }}>{a.reason || '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function AgentLog() {
  return (<><AgentControl /><ActionsTable /></>);
}
