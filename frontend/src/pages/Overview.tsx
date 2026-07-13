import { api } from '../services/api';
import { useLive, Loading, Empty, normalizeRegions, healthClass } from './common';
import type { OnNavigate } from './Dashboard';

export default function Overview({ onNavigate }: { onNavigate?: OnNavigate }) {
  const { data, loading } = useLive(async () => {
    const [r, f, c, m, s, n] = await Promise.all([
      api.regions(), api.fleet(), api.getConfig(), api.metrics().catch(() => ({ summary: {} })),
      api.getSchedules().catch(() => ({ schedules: [] })), api.network().catch(() => ({ selections: [] })),
    ]);
    return {
      regions: r.regions ?? [], fleet: f.fleet_state ?? [], instances: f.instances ?? [],
      cfg: c ?? {}, metrics: m.summary ?? {}, schedules: s.schedules ?? [], selections: n.selections ?? [],
    };
  }, 15000);
  if (loading && !data) return <Loading />;

  const R = normalizeRegions(data?.regions);
  const regions: string[] = R.ids;
  const fleet: any[] = data?.fleet ?? [];
  const instances: any[] = data?.instances ?? [];
  const cfg: any = data?.cfg ?? {};
  const met: any = data?.metrics ?? {};
  const schedules: any[] = data?.schedules ?? [];
  const selections: any[] = data?.selections ?? [];
  const base = Number(cfg.base_count ?? 0);

  const per: Record<string, { spot: number; od: number }> = {};
  regions.forEach((r) => (per[r] = { spot: 0, od: 0 }));
  fleet.forEach((f: any) => {
    per[f.region] = per[f.region] || { spot: 0, od: 0 };
    if (f.asg_kind === 'spot') per[f.region].spot = Number(f.healthy || 0);
    else per[f.region].od = Number(f.healthy || 0);
  });
  const healthy = Object.values(per).reduce((a, x) => a + x.spot + x.od, 0);
  const active = Object.values(per).filter((x) => x.spot + x.od > 0).length;
  const peak = Math.max(1, ...Object.values(per).map((x) => x.spot + x.od));

  // ---- 接入完整性 ----
  const cfgRegions = cfg.regions || {};
  const amiDone = regions.filter((r) => cfgRegions[r]?.ami_arn && cfgRegions[r]?.enabled);
  // 一个区算「网络就绪」= 已 provision(Config 有 provisioned_vpc)或向导里存过 VPC 选择;
  // 直接调 provision(未走向导存选择)的区靠 provisioned_vpc 计入,与环境向导「已创建」口径一致。
  const netDone = regions.filter((r) =>
    cfgRegions[r]?.provisioned_vpc ||
    (selections || []).some((s: any) => s.region === r && (s.create_new || s.vpc_id)),
  );
  const capDone = base > 0 || schedules.some((s: any) => s.enabled);
  const steps = [
    { key: 'ami', label: '模型 / AMI', sub: '每区填入 AMI ARN 并启用', done: amiDone.length, total: regions.length, view: 'env' as const },
    { key: 'net', label: '网络 (VPC / 子网)', sub: '每区选择现有或新建 VPC', done: netDone.length, total: regions.length, view: 'env' as const },
    { key: 'cap', label: '容量(基础或活动)', sub: '设置基础数量或启用定时活动', done: capDone ? 1 : 0, total: 1, view: 'schedules' as const },
  ];
  const doneCount = steps.filter((s) => s.done >= s.total).length;
  const configured = doneCount === steps.length;
  const dotFor = (s: typeof steps[number]) => (s.done >= s.total ? 'g' : s.done > 0 ? 'a' : 'm');
  const healthDot = base === 0 ? 'm' : healthy >= base ? 'g' : 'a';

  return (
    <>
      {!configured && (
        <div className="card checklist" style={{ marginBottom: 20 }}>
          <h3 style={{ marginBottom: 4 }}>接入清单 <span className="faint" style={{ fontWeight: 400, fontSize: 13 }}>已完成 {doneCount} / {steps.length}</span></h3>
          <p className="hint" style={{ margin: '0 0 6px' }}>完成以下步骤后,平台即可在活动窗口自动拉起 GPU 并经 Global Accelerator 对外服务。</p>
          {steps.map((s) => (
            <div className="ck-row" key={s.key}>
              <span className={`dot ${dotFor(s)}`} />
              <div className="ck-main"><b>{s.label}</b><div className="ck-sub">{s.sub}</div></div>
              <span className={`tag ${s.done >= s.total ? 'g' : s.done > 0 ? 'a' : ''}`}>{s.total > 1 ? `${s.done}/${s.total} 区` : (s.done ? '已配置' : '未配置')}</span>
              <button className="btn btn-sm" style={{ marginLeft: 4 }} onClick={() => onNavigate?.(s.view)}>去配置 →</button>
            </div>
          ))}
        </div>
      )}

      <div className="kpis">
        <div className="kpi"><div className="lbl">基础台数</div><div className="v b-blue">{base}<small> 台</small></div><div className="d">常驻;活动窗口叠加</div></div>
        <div className="kpi"><div className="lbl"><span className={`dot ${healthDot}`} />健康节点</div><div className="v b-teal">{healthy}<small> / {base}</small></div><div className="d">跨区实时就绪</div></div>
        <div className="kpi"><div className="lbl">总吞吐</div><div className="v">{met.total_qps ?? 0}<small> QPS</small></div><div className="d">P95 {met.avg_latency_p95 ?? 0} ms</div></div>
        <div className="kpi"><div className="lbl">机型策略</div><div className="v" style={{ fontSize: 18 }}>{((data?.cfg?.instance_type_priority as string[]) ?? ['p4d.24xlarge', 'p4de.24xlarge']).map((t) => t.replace('.24xlarge', '')).join(' › ')}</div><div className="d">全按需 · 顺序即优先级(定时活动页可改)</div></div>
      </div>

      <div className="section-t">区域容量 · {active}/{regions.length} 活跃</div>
      <div className="grid3">
        {regions.map((r) => {
          const d = per[r] || { spot: 0, od: 0 };
          const h = d.spot + d.od;
          const preferred = r === R.priorityRegion;
          return (
            <div className="region-card" key={r}>
              <h4>{r}<span className={`dot ${h > 0 ? 'g' : 'm'}`} /></h4>
              <div className="sub">{R.label[r]}{preferred ? ' ★' : ''}</div>
              <div className="bar"><i style={{ width: `${Math.min(100, (h / peak) * 100)}%` }} /></div>
              <div style={{ marginTop: 14 }}>
                <div className="row"><span>健康节点(按需)</span><b className="b-teal">{h}</b></div>
                <div className="row"><span>GA 权重</span><b>{healthy > 0 ? Math.round((h / healthy) * 100) : 0}%</b></div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="section-t">最近实例</div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>实例 ID</th><th>区域</th><th>AZ</th><th>机型</th><th>生命周期</th><th>模型就绪</th><th>开机时间</th><th>终止时间</th></tr></thead>
            <tbody>
              {instances.length === 0 ? (
                <tr><td colSpan={8}><Empty icon="🌙" hint="活动窗口开始后按排期自动拉起 GPU(按需);也可在「定时活动」设置基础常驻台数。">当前无运行实例</Empty></td></tr>
              ) : [...instances].sort((a, b) => {
                const t = (a.terminated_at ? 1 : 0) - (b.terminated_at ? 1 : 0);  // 已终止沉底
                if (t !== 0) return t;
                return new Date(b.launch_time || 0).getTime() - new Date(a.launch_time || 0).getTime();  // 开机时间降序
              }).slice(0, 12).map((i) => (
                <tr key={i.instance_id} style={i.terminated_at ? { opacity: 0.5 } : undefined}>
                  <td>{i.instance_id}</td><td>{i.region}</td><td>{i.az}</td>
                  <td><span className="tag v">{i.type}</span></td><td>{i.lifecycle}</td>
                  <td>{i.target_state !== undefined
                    ? <><span className={`dot ${i.ready ? 'g' : (i.target_state === 'initial' ? 'a' : 'm')}`} /> {i.ready ? '就绪' : (i.target_state === 'initial' ? '加载中' : '未就绪')}</>
                    : <><span className={`dot ${healthClass(i.health)}`} /> {i.health || '—'}</>}</td>
                  <td className="faint">{i.launch_time ? new Date(i.launch_time).toLocaleString() : '—'}</td>
                  <td className="faint">{i.terminated_at ? new Date(i.terminated_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
