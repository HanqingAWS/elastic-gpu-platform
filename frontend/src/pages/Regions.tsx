import { useState } from 'react';
import { api } from '../services/api';
import { useLive, Loading, Banner, Empty, normalizeRegions } from './common';

// UTC+8(Asia/Shanghai)日期 —— 与页面显示一致
const sgDay = (offset = 0) => new Date(Date.now() + offset * 86400000 + 8 * 3600000).toISOString().slice(0, 10);

export default function Regions() {
  const [range, setRange] = useState<{ from: string; to: string }>({ from: '', to: '' });
  const { data, loading } = useLive(async () => {
    const [r, f, c] = await Promise.all([
      api.regions(), api.fleet(),
      api.runningHours(range.from || undefined, range.to || undefined).catch(() => ({ today: [], by_day: [], totals: {} })),
    ]);
    return { regions: r.regions ?? [], fleet: f.fleet_state ?? [], cost: c };
  }, 15000, `${range.from}|${range.to}`);
  if (loading && !data) return <Loading />;

  const R = normalizeRegions(data?.regions);
  const regions: string[] = R.ids;
  const fleet: any[] = data?.fleet ?? [];
  const cost: any = data?.cost ?? { today: [], by_day: [], totals: {} };
  const todayH: Record<string, any> = {};
  (cost.today || []).forEach((t: any) => (todayH[t.region] = t));
  const ct = cost.totals || {};

  const per: Record<string, { spot: number; od: number }> = {};
  regions.forEach((r) => (per[r] = { spot: 0, od: 0 }));
  fleet.forEach((f: any) => {
    per[f.region] = per[f.region] || { spot: 0, od: 0 };
    if (f.asg_kind === 'spot') per[f.region].spot = Number(f.healthy || 0);
    else per[f.region].od = Number(f.healthy || 0);
  });

  const totals = regions.map((r) => (per[r]?.spot || 0) + (per[r]?.od || 0));
  const total = totals.reduce((a, b) => a + b, 0);
  // GA 权重 = 各区健康台数占比(纯按台数,无区域保底),使每台流量均摊。
  const weight = (r: string) => {
    const h = (per[r]?.spot || 0) + (per[r]?.od || 0);
    return total > 0 ? Math.round((h / total) * 100) : 0;
  };
  const peak = Math.max(1, ...totals);

  return (
    <>
      <Banner>目标:<b>每台节点流量均摊</b>。各区 GA endpoint group 的 TrafficDial 与该区健康台数成正比(纯按台数、<b>无区域保底</b>),区内由 ALB 最少未决请求(LOR)在各台间均摊 —— 跨区按台数 + 区内均摊 = 每台大致相同。台数最多的区 dial=100,其余按占比缩放,0 台移出轮转。(★ 仅表示优先拉起顺序,不影响权重)</Banner>
      <div className="grid3">
        {regions.map((r) => {
          const d = per[r] || { spot: 0, od: 0 };
          const h = d.spot + d.od;
          const w = weight(r);
          const preferred = r === R.priorityRegion;
          return (
            <div className="region-card" key={r}>
              <h4>{r}<span className={`dot ${h > 0 ? 'g' : 'm'}`} /></h4>
              <div style={{ color: 'var(--faint)', fontFamily: 'var(--mono)', fontSize: 11, marginTop: 2 }}>
                {R.label[r]}{preferred ? ' ★' : ''}
              </div>
              <div className="bar"><i style={{ width: `${Math.min(100, (h / peak) * 100)}%` }} /></div>
              <div style={{ marginTop: 14 }}>
                <div className="row"><span>健康节点(按需)</span><b className="b-teal">{h}</b></div>
                <div className="row"><span>GA 权重(流量占比)</span><b className="b-blue">{w}%</b></div>
                <div className="row"><span>今日运行时长</span><b><span className="b-amber">{((todayH[r]?.od_hours ?? 0) + (todayH[r]?.spot_hours ?? 0)).toFixed ? (Number(todayH[r]?.od_hours ?? 0) + Number(todayH[r]?.spot_hours ?? 0)).toFixed(1) : 0}h</span></b></div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="section-t">运行时长历史(按天 · UTC+8 · 永久保留)</div>
      <div className="subnav" style={{ alignItems: 'center' }}>
        <button className={!range.from && !range.to ? 'on' : ''} onClick={() => setRange({ from: '', to: '' })}>全部</button>
        <button className={range.from === sgDay(-6) && !range.to ? 'on' : ''} onClick={() => setRange({ from: sgDay(-6), to: '' })}>近 7 天</button>
        <button className={range.from === sgDay(-29) && !range.to ? 'on' : ''} onClick={() => setRange({ from: sgDay(-29), to: '' })}>近 30 天</button>
        <span className="faint" style={{ fontSize: 12 }}>从</span>
        <input type="date" value={range.from} max={sgDay(0)} onChange={(e) => setRange((s) => ({ ...s, from: e.target.value }))} style={{ width: 'auto' }} />
        <span className="faint" style={{ fontSize: 12 }}>到</span>
        <input type="date" value={range.to} max={sgDay(0)} onChange={(e) => setRange((s) => ({ ...s, to: e.target.value }))} style={{ width: 'auto' }} />
      </div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>日期(UTC)</th><th>Spot 小时</th><th>On-Demand 小时</th><th>合计小时</th></tr></thead>
            <tbody>
              {(cost.by_day || []).length === 0 ? (
                <tr><td colSpan={4}><Empty icon="📊" hint="Agent 每 tick 累计各区运行实例的时长(计费兜底,不依赖 Spot 定价)。有实例运行后此处按天累加。">暂无运行时长记录</Empty></td></tr>
              ) : (cost.by_day || []).map((d: any) => (
                <tr key={d.date}>
                  <td>{d.date}</td><td className="b-teal">{d.spot_hours}</td><td className="b-amber">{d.od_hours}</td>
                  <td>{(Number(d.spot_hours) + Number(d.od_hours)).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p className="hint" style={{ marginTop: 10 }}>
        {ct.basis ?? '近 7 天'}合计:Spot <b className="b-teal">{ct.spot_hours_7d ?? 0}h</b> · OD <b className="b-amber">{ct.od_hours_7d ?? 0}h</b> ·
        估算成本 <b className="b-blue">≈ ${ct.est_usd_7d ?? 0}</b>
        <span className="faint"> (估算:Spot ${ct.spot_rate ?? '—'}/h、OD ${ct.od_rate ?? '—'}/h,按 {ct.assumed_type ?? 'p4de.24xlarge'} 计;精确账单以 AWS Cost Explorer 为准)</span>
      </p>
    </>
  );
}
