import { useState } from 'react';
import { api } from '../services/api';
import { useLive, Loading, Empty, Banner, normalizeRegions, fmt } from './common';

export default function Monitoring() {
  const [tab, setTab] = useState<'perf' | 'spot'>('perf');
  const { data, loading } = useLive(async () => {
    const [m, s, n] = await Promise.all([
      api.metrics(), api.spotEvents().catch(() => null), api.regions().catch(() => ({ regions: [] })),
    ]);
    return { m, s, regions: n.regions ?? [] };
  }, 10000);
  if (loading && !data) return <Loading />;
  const m = data?.m ?? {};
  const spot = data?.s ?? null;
  const R = normalizeRegions(data?.regions);

  return (
    <>
      <div className="subnav">
        <button className={tab === 'perf' ? 'on' : ''} onClick={() => setTab('perf')}>性能监控</button>
        <button className={tab === 'spot' ? 'on' : ''} onClick={() => setTab('spot')}>Spot 回收统计</button>
      </div>
      {tab === 'perf' ? <Perf m={m} R={R} /> : <Spot spot={spot} R={R} />}
    </>
  );
}

function Perf({ m, R }: { m: any; R: any }) {
  const s = m?.summary ?? {};
  const perRegion: any[] = m?.per_region ?? [];
  const rows: any[] = m?.instances ?? [];
  return (
    <>
      <div className="kpis">
        <div className="kpi"><div className="lbl">总吞吐 QPS</div><div className="v b-teal">{s.total_qps ?? 0}</div><div className="d">全部就绪节点合计</div></div>
        <div className="kpi"><div className="lbl">Token 吞吐</div><div className="v b-blue">{s.total_tokens_per_sec ?? 0}<small> tok/s</small></div><div className="d">生成速率合计</div></div>
        <div className="kpi"><div className="lbl">平均 P95 延迟</div><div className="v b-amber">{s.avg_latency_p95 ?? 0}<small> ms</small></div><div className="d">各节点 P95 均值</div></div>
        <div className="kpi"><div className="lbl">上报节点</div><div className="v">{s.reporting_nodes ?? 0}</div><div className="d">正在上报 /metrics</div></div>
      </div>
      {(!rows.length) && <Banner>Agent 每 tick 抓取各节点 <b>/metrics</b>(QPS / 延迟分位 / token 吞吐)写入 MetricsRollup(留存 90 天)。当前无运行节点,窗口开始并有实例注册后此处实时呈现。</Banner>}
      <div className="section-t">分区吞吐</div>
      <div className="grid3" style={{ marginBottom: 20 }}>
        {(perRegion.length ? perRegion : R.ids.map((region: string) => ({ region }))).map((r: any) => (
          <div className="region-card" key={r.region}>
            <h4>{r.region}</h4>
            <div style={{ color: 'var(--faint)', fontFamily: 'var(--mono)', fontSize: 11, marginTop: 2 }}>{R.label[r.region] || ''}</div>
            <div style={{ marginTop: 12 }}>
              <div className="row"><span>QPS</span><b className="b-teal">{r.qps ?? 0}</b></div>
              <div className="row"><span>Token/s</span><b className="b-blue">{r.tokens_per_sec ?? 0}</b></div>
              <div className="row"><span>节点数</span><b>{r.nodes ?? 0}</b></div>
            </div>
          </div>
        ))}
      </div>
      <div className="section-t">各节点实时指标</div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table>
          <thead><tr><th>实例</th><th>区域</th><th>机型</th><th>QPS</th><th>P50 ms</th><th>P95 ms</th><th>tok/s</th><th>采样</th></tr></thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={8}><Empty>暂无指标数据</Empty></td></tr>
            ) : rows.map((r) => (
              <tr key={r.instance_id}>
                <td>{r.instance_id}</td><td>{r.region}</td>
                <td><span className="tag v">{r.type || '—'}</span></td>
                <td className="b-teal">{r.qps}</td><td>{r.latency_p50}</td><td className="b-amber">{r.latency_p95}</td>
                <td className="b-blue">{r.tokens_per_sec}</td><td className="faint">{fmt(r.ts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Spot({ spot, R }: { spot: any; R: any }) {
  const sum = spot?.summary ?? {};
  const events: any[] = spot?.events ?? [];
  const byRegion: any[] = sum.by_region ?? [];
  const byType: any[] = sum.by_type ?? [];
  const retention = spot?.retention_days ?? 90;
  return (
    <>
      <div className="kpis">
        <div className="kpi"><div className="lbl">累计回收</div><div className="v b-rose">{sum.total ?? 0}</div><div className="d">Spot 被回收次数</div></div>
        <div className="kpi"><div className="lbl">近 30 天</div><div className="v b-amber">{sum.last_30d ?? 0}</div><div className="d">最近回收事件</div></div>
        <div className="kpi"><div className="lbl">覆盖区域</div><div className="v">{byRegion.length}<small> / {R.ids.length}</small></div><div className="d">发生回收的区</div></div>
        <div className="kpi"><div className="lbl">数据留存</div><div className="v b-teal">{retention}<small> 天</small></div><div className="d">TTL 自动清理</div></div>
      </div>

      {(!events.length) && <Banner>Agent 从控制平面识别被 Spot 回收(StateReason=Server.SpotInstanceTermination)的本平台实例,写入 SpotEvents(留存 {retention} 天)。当前尚无回收记录。CapacityRebalance 已在 ASG 开启以提前腾挪。</Banner>}

      <div className="rowflex" style={{ marginBottom: 20 }}>
        <div className="card">
          <h3>按区域</h3>
          {byRegion.length === 0 ? <Empty>无</Empty> : byRegion.map((r) => (
            <div className="kv" key={r.region}><span className="k">{r.region} <span className="faint">{R.label[r.region] || ''}</span></span><span className="val b-rose">{r.count}</span></div>
          ))}
        </div>
        <div className="card">
          <h3>按类型</h3>
          {byType.length === 0 ? <Empty>无</Empty> : byType.map((t) => (
            <div className="kv" key={t.type}><span className="k">{t.type}</span><span className="val b-amber">{t.count}</span></div>
          ))}
        </div>
      </div>

      <div className="section-t">回收事件明细</div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table>
          <thead><tr><th>时间</th><th>区域</th><th>实例</th><th>AZ</th><th>机型</th><th>类型</th><th>原因</th></tr></thead>
          <tbody>
            {events.length === 0 ? (
              <tr><td colSpan={7}><Empty>暂无回收事件</Empty></td></tr>
            ) : events.map((e, i) => (
              <tr key={i}>
                <td className="faint">{fmt(e.ts)}</td><td>{e.region}</td><td>{e.instance_id}</td><td>{e.az || '—'}</td>
                <td><span className="tag v">{e.instance_type || '—'}</span></td>
                <td><span className="tag r">{e.event_type || 'reclaimed'}</span></td>
                <td className="faint" style={{ fontSize: 12 }}>{e.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
