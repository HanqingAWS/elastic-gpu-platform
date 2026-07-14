import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { useLive, Loading, Empty, Banner, REGION_LABEL, healthClass, Copy } from './common';

// GA 拓扑主体(选定某个 GA 后展示):加速器 DNS/静态 IP + 每监听器每区 endpoint group。
function Topo({ data }: { data: any }) {
  const a = data.accelerator;
  const listeners: any[] = data.listeners ?? [];
  return (
    <>
      <Banner>统一入口:DNS 指向下方 <b>静态 IP / DNS</b>,anycast 就近接入。平台运行时仅调整各区 endpoint 权重与 TrafficDial,IP 恒定不变。</Banner>

      <div className="rowflex">
        <div className="card">
          <h3>加速器 <span className={`tag ${a.status === 'DEPLOYED' ? 'g' : 'a'}`}>{a.status}</span></h3>
          <div className="kv"><span className="k">名称</span><span className="val">{a.name}</span></div>
          <div className="kv"><span className="k">DNS</span><span className="val">{a.dns_name}<Copy text={a.dns_name} /></span></div>
          <div className="kv"><span className="k">IP 类型</span><span className="val">{a.ip_type}</span></div>
          <div className="kv"><span className="k">启用</span><span className="val">{a.enabled ? '是' : '否'}</span></div>
        </div>
        <div className="card">
          <h3>静态 Anycast IP</h3>
          {(a.static_ips ?? []).length === 0 ? <Empty>无</Empty> :
            a.static_ips.map((ip: string) => (
              <div className="kv" key={ip}><span className="k">Elastic IP</span><span className="val b-teal">{ip}<Copy text={ip} /></span></div>
            ))}
          <div style={{ marginTop: 10 }} className="arn">ARN: {a.arn}</div>
        </div>
      </div>

      {listeners.map((l) => (
        <div className="card" key={l.listener_arn}>
          <h3>监听器
            <span className="chip">{l.protocol} · {(l.port_ranges || []).map((p: any) => p.FromPort === p.ToPort ? p.FromPort : `${p.FromPort}-${p.ToPort}`).join(', ')}</span>
          </h3>
          <table>
            <thead><tr><th>区域</th><th>TrafficDial</th><th>健康检查</th><th>Endpoints(ALB)</th></tr></thead>
            <tbody>
              {(l.endpoint_groups || []).length === 0 ? (
                <tr><td colSpan={4}><Empty>无 endpoint group</Empty></td></tr>
              ) : l.endpoint_groups.map((eg: any) => (
                <tr key={eg.region}>
                  <td>{eg.region} <span className="faint">{REGION_LABEL[eg.region] || ''}</span></td>
                  <td className="b-blue">{eg.traffic_dial}%</td>
                  <td className="faint">{eg.health_check_port ? `${eg.health_check_port}${eg.health_check_path || ''}` : '—'}</td>
                  <td>
                    {(eg.endpoints || []).length === 0 ? <span className="faint">空（窗口未激活）</span> :
                      eg.endpoints.map((e: any, i: number) => (
                        <div key={i} style={{ marginBottom: 4 }}>
                          <span className={`dot ${healthClass(e.health_state)}`} /> weight {e.weight}
                          <span className="arn" style={{ marginLeft: 8 }}>{e.endpoint_id}</span>
                        </div>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}

export default function GlobalAccelerator() {
  // GA 不再由 CDK 建 → 页面不自动选任何 GA。默认预选 Config 里 provision 时所选的平台 GA;否则留空由用户下拉自选。
  const [selectedArn, setSelectedArn] = useState<string>('');
  const [accels, setAccels] = useState<any[]>([]);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.getConfig().catch(() => ({} as any)),
      api.accelerators().catch(() => ({ accelerators: [] })),
    ]).then(([cfg, ac]: any[]) => {
      if (!alive) return;
      setAccels(ac.accelerators || []);
      if (cfg?.ga_accelerator_arn) setSelectedArn(cfg.ga_accelerator_arn);
    });
    return () => { alive = false; };
  }, []);

  const { data, loading } = useLive(
    () => (selectedArn ? api.ga(selectedArn) : Promise.resolve({ configured: false } as any)),
    20000,
    selectedArn,
  );

  return (
    <>
      <div className="field" style={{ maxWidth: 680, marginBottom: 14 }}>
        <label>选择 Global Accelerator（默认为已配置的平台 GA;账号内全部 GA 供选）</label>
        <select value={selectedArn} onChange={(e) => setSelectedArn(e.target.value)}>
          <option value="">— 请选择 GA —</option>
          {accels.map((g) => <option key={g.arn} value={g.arn}>{g.name} · {g.dns}</option>)}
        </select>
      </div>

      {!selectedArn ? (
        <Empty>请从上方选择要查看的 Global Accelerator（本平台不自动展示账号内其它工作负载的 GA）。</Empty>
      ) : loading && !data ? (
        <Loading />
      ) : !data?.configured || !data?.accelerator ? (
        <Empty>未发现该 Global Accelerator 的拓扑{data?.error ? ` —— ${data.error}` : ''}</Empty>
      ) : (
        <Topo data={data} />
      )}
    </>
  );
}
