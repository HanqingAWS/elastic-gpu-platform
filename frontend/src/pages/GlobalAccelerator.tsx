import { api } from '../services/api';
import { useLive, Loading, Empty, Banner, REGION_LABEL, healthClass, Copy } from './common';

export default function GlobalAccelerator() {
  const { data, loading } = useLive(() => api.ga(), 20000);
  if (loading && !data) return <Loading />;

  if (!data?.configured || !data?.accelerator) {
    return <Empty>未发现 Global Accelerator{data?.error ? ` —— ${data.error}` : '（尚未部署或无权限）'}</Empty>;
  }
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
