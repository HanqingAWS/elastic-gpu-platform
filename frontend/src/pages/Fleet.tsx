import { api } from '../services/api';
import { useLive, Loading, Empty, healthClass, REGION_LABEL, fmt } from './common';

export default function Fleet() {
  const { data, loading } = useLive(() => api.fleet(), 15000);
  if (loading && !data) return <Loading />;
  const fleetState: any[] = data?.fleet_state ?? [];
  const instances: any[] = data?.instances ?? [];

  return (
    <>
      <div className="section-t">ASG 状态</div>
      <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 20 }}>
        <table>
          <thead><tr><th>区域</th><th>类型</th><th>期望</th><th>健康</th><th>更新时间</th></tr></thead>
          <tbody>
            {fleetState.length === 0 ? (
              <tr><td colSpan={5}><Empty>暂无 ASG —— 完成网络 provisioning 后,窗口到点自动拉起</Empty></td></tr>
            ) : fleetState.map((f, i) => (
              <tr key={i}>
                <td>{f.region} <span className="faint">{REGION_LABEL[f.region] || ''}</span></td>
                <td><span className={`tag ${f.asg_kind === 'spot' ? 'b' : 'a'}`}>{f.asg_kind}</span></td>
                <td>{f.desired ?? 0}</td>
                <td className={Number(f.healthy) >= Number(f.desired) ? 'b-teal' : 'b-amber'}>{f.healthy ?? 0}</td>
                <td className="faint">{fmt(f.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section-t">实例清单</div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>实例 ID</th><th>区域</th><th>AZ</th><th>机型</th><th>生命周期</th><th>模型就绪</th><th>开机时间</th><th>终止时间</th></tr></thead>
            <tbody>
              {instances.length === 0 ? (
                <tr><td colSpan={8}><Empty>🌙 当前无运行实例 —— 活动窗口开始后自动拉起 GPU(按需)</Empty></td></tr>
              ) : [...instances].sort((a, b) => {
                const t = (a.terminated_at ? 1 : 0) - (b.terminated_at ? 1 : 0);  // 已终止沉底
                if (t !== 0) return t;
                return new Date(b.launch_time || 0).getTime() - new Date(a.launch_time || 0).getTime();  // 开机时间降序
              }).map((i) => (
                <tr key={i.instance_id} style={i.terminated_at ? { opacity: 0.5 } : undefined}>
                  <td>{i.instance_id}</td><td>{i.region}</td><td>{i.az}</td>
                  <td><span className="tag v">{i.type}</span></td>
                  <td>{i.lifecycle}</td>
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
