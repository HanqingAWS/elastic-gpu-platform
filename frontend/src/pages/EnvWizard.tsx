import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { REGIONS, REGION_LABEL, Banner, Empty } from './common';

// 拓扑式配置:Global Accelerator → 各区 ALB → 各区 GPU ASG(全按需)。
// 节点颜色即状态:灰=未配置 · 蓝=已配置待创建 · 绿=资源已创建(ASG 存在,真实检测) · 琥珀=部分。
// 点任一区列 → 右侧抽屉配置该区(VPC/子网/SG/密钥/AMI)+ 暂存 / 创建。

type NodeState = 'gray' | 'blue' | 'green' | 'amber';
const STATE_LABEL: Record<NodeState, string> = { gray: '未配置', blue: '已配置 · 待创建', green: '已创建 ✓', amber: '部分 · 需补齐' };

export default function EnvWizard() {
  const [open, setOpen] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [g, setG] = useState<{ net: any[]; cfg: any; ga: any; status: Record<string, any> } | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [net, cfg, ga] = await Promise.all([
        api.network().catch(() => ({ selections: [] })),
        api.getConfig().catch(() => ({})),
        api.ga().catch(() => null),
      ]);
      const sels = net.selections || [];
      const status: Record<string, any> = {};
      await Promise.all(REGIONS.map(async (r) => {
        const sel = sels.find((x: any) => x.region === r);
        status[r] = await api.regionStatus(r, sel?.vpc_id || undefined).catch(() => null);
      }));
      if (alive) setG({ net: sels, cfg: cfg.regions || {}, ga, status });
    })();
    return () => { alive = false; };
  }, [tick]);

  const stateOf = (r: string): NodeState => {
    if (!g) return 'gray';
    const sel = g.net.find((x: any) => x.region === r);
    const rc = g.cfg[r] || {};
    const saved = !!(sel && (sel.create_new || sel.vpc_id) && rc.ami_arn);
    const st = g.status[r]?.state;
    if (st === 'created') return 'green';
    if (st === 'partial') return 'amber';
    return saved ? 'blue' : 'gray';
  };

  const anyGreen = g ? REGIONS.some((r) => stateOf(r) === 'green') : false;

  return (
    <>
      <Banner>
        点任一<b>区域节点</b>配置该区。颜色即状态:
        <b style={{ color: 'var(--faint)' }}> 灰=未配置</b> →
        <b style={{ color: 'var(--blue)' }}> 蓝=已配置·待创建</b> →
        <b style={{ color: 'var(--teal)' }}> 绿=已创建(ASG 存在)</b>。
        链路 <b>Global Accelerator → 各区 ALB → GPU ASG(按需)</b>;台数不在此设置,去「定时活动」配置。
      </Banner>

      <div className="topo">
        {/* GA 根节点 */}
        <div className={`tnode tn-ga ${anyGreen ? 'tn-green' : ''}`}>
          <div className="tn-t">Global Accelerator</div>
          <div className="tn-s mono">{g?.ga?.accelerator?.dns_name || 'nlp-platform-dev'}</div>
          {g?.ga?.accelerator?.static_ips?.length
            ? <div className="tn-s mono faint">{g.ga.accelerator.static_ips.join('  ·  ')}</div> : null}
        </div>
        <div className="topo-stem" />
        <div className="topo-bus" />

        {/* 三区列 */}
        <div className="topo-cols">
          {REGIONS.map((r) => {
            const s = stateOf(r);
            const st = g?.status?.[r];
            const preferred = r === 'us-east-1';
            return (
              <div className="topo-col" key={r}>
                <div className="topo-drop" />
                <div className={`tnode clickable tn-${s}`} onClick={() => setOpen(r)} title="点击配置该区">
                  <div className="tn-t">{r} {preferred ? <span className="star">★</span> : null}</div>
                  <div className="tn-s faint">{REGION_LABEL[r]} · ALB</div>
                  <div className={`tn-badge b-${s}`}>{STATE_LABEL[s]}</div>
                  {st?.alb?.exists ? <div className="tn-s mono faint">ALB {st.alb.state}</div> : null}
                </div>
                <div className="topo-link" />
                <div className={`tnode clickable tn-${s}`} onClick={() => setOpen(r)} title="点击配置该区">
                  <div className="tn-t">GPU ASG(按需)</div>
                  {s === 'green'
                    ? <div className="tn-s mono">desired {st?.od_asg?.desired ?? 0} · 实例 {st?.od_asg?.instances ?? 0}</div>
                    : <div className="tn-s faint">{s === 'blue' ? '待创建(点击→创建)' : s === 'amber' ? '缺一个,需重新创建' : '未创建'}</div>}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {open && <RegionDrawer region={open} onClose={() => setOpen(null)} onChanged={() => setTick((t) => t + 1)} />}
    </>
  );
}

// ---------- 单区配置抽屉 ----------
function RegionDrawer({ region, onClose, onChanged }: { region: string; onClose: () => void; onChanged: () => void }) {
  const [loaded, setLoaded] = useState(false);
  const [createNew, setCreateNew] = useState(false);
  const [vpcs, setVpcs] = useState<any[] | null>(null);
  const [vpcId, setVpcId] = useState('');
  const [subnets, setSubnets] = useState<any[]>([]);
  const [selSubnets, setSelSubnets] = useState<string[]>([]);
  const [sgs, setSgs] = useState<any[]>([]);
  const [sgId, setSgId] = useState('');
  const [keys, setKeys] = useState<any[]>([]);
  const [keyName, setKeyName] = useState('');
  const [ackOpenSg, setAckOpenSg] = useState(false);
  const [amiArn, setAmiArn] = useState('');
  const [servingPort, setServingPort] = useState(8000);
  const [healthPath, setHealthPath] = useState('/health');
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [steps, setSteps] = useState<any[]>([]);
  const [rstat, setRstat] = useState<any>(null);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);

  useEffect(() => {
    (async () => {
      const [net, cfg, kp, vp] = await Promise.all([
        api.network().catch(() => ({ selections: [] })), api.getConfig().catch(() => ({})),
        api.keyPairs(region).catch(() => ({ key_pairs: [] })), api.vpcs(region).catch(() => ({ vpcs: null })),
      ]);
      setKeys(kp.key_pairs || []);
      if (vp.vpcs) setVpcs(vp.vpcs);
      const sel = (net.selections || []).find((x: any) => x.region === region);
      if (sel) {
        setCreateNew(!!sel.create_new); setVpcId(sel.vpc_id || ''); setSelSubnets(sel.subnet_ids || []);
        setSgId(sel.sg_id || ''); setKeyName(sel.key_name || '');
        if (sel.vpc_id && !sel.create_new) {
          try {
            const [gr, s] = await Promise.all([api.securityGroups(region, sel.vpc_id), api.subnets(region, sel.vpc_id)]);
            setSgs(gr.security_groups || []); setSubnets(s.subnets || []);
          } catch { /* */ }
        }
      }
      const rc = (cfg.regions || {})[region] || {};
      setAmiArn(rc.ami_arn || ''); setServingPort(rc.serving_port || 8000);
      setHealthPath(rc.health_path || '/health'); setEnabled(rc.enabled ?? true);
      setLoaded(true);
      api.regionStatus(region, sel?.vpc_id || undefined).then(setRstat).catch(() => {});
    })();
  }, [region]);

  const pickVpc = async (id: string) => {
    setVpcId(id); setCreateNew(false); setSelSubnets([]); setSgId(''); setBusy('subnets'); setSubnets([]); setSgs([]);
    try {
      const r = await api.subnets(region, id); setSubnets(r.subnets || []);
      const gr = await api.securityGroups(region, id); setSgs(gr.security_groups || []);
    } catch (e: any) { setToast({ ok: false, msg: e.message }); }
    finally { setBusy(null); }
  };
  const toggleSubnet = (id: string) =>
    setSelSubnets((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const saveAll = async (): Promise<boolean> => {
    setToast(null);
    try {
      await api.putNetwork({ region, vpc_id: createNew ? null : vpcId, subnet_ids: createNew ? [] : selSubnets, create_new: createNew, sg_id: createNew ? null : (sgId || null), key_name: keyName || null });
      await api.putConfig({ regions: { [region]: { ami_arn: amiArn, serving_port: Number(servingPort), health_path: healthPath, enabled: enabled && !!amiArn } } });
      return true;
    } catch (e: any) { setToast({ ok: false, msg: e.message }); return false; }
  };
  const save = async () => { setBusy('save'); if (await saveAll()) { setToast({ ok: true, msg: '配置已保存 ✓' }); onChanged(); } setBusy(null); };

  const provision = async () => {
    setBusy('provision'); setSteps([]);
    if (!(await saveAll())) { setBusy(null); return; }
    setToast({ ok: true, msg: '已提交,后台创建中(约 1–3 分钟,可留在本页看进度)…' });
    try {
      const { run_id } = await api.provision({
        region, ami_id: amiArn, vpc_id: createNew ? null : (vpcId || null),
        subnet_ids: createNew ? null : (selSubnets.length ? selSubnets : null),
        sg_id: createNew ? null : (sgId || null), key_name: keyName || null,
        serving_port: Number(servingPort), health_path: healthPath, metrics_port: Number(servingPort), dry_run: false,
      });
      let st: any = null;
      for (let i = 0; i < 160; i++) {          // 轮询最长约 8 分钟
        await new Promise((res) => setTimeout(res, 3000));
        st = await api.provisionStatus(run_id).catch(() => null);
        if (st?.steps) setSteps(st.steps);     // 实时进度
        if (st?.finished) break;
      }
      if (st?.status === 'succeeded') {
        setToast({ ok: true, msg: '数据面资源已创建(ASG desired=0,未开 GPU)✓' });
        onChanged();
        api.regionStatus(region, st.vpc_id || (createNew ? undefined : vpcId)).then(setRstat).catch(() => {});
      } else if (st?.status === 'failed') {
        setToast({ ok: false, msg: st.error || '创建失败' });
      } else {
        setToast({ ok: false, msg: '仍在进行或超时,请稍后回到本页/「区域」页确认 ASG' });
      }
    } catch (e: any) { setToast({ ok: false, msg: e.message }); }
    finally { setBusy(null); }
  };

  const sgOpen = !!sgs.find((gr) => gr.sg_id === sgId)?.open_to_world;
  const blockOpen = sgOpen && !ackOpenSg;
  const created = rstat?.state === 'created';

  return (
    <div className="drawer-back" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <h3 style={{ margin: 0 }}>{region} <span className="faint" style={{ fontWeight: 400, fontSize: 13 }}>· {REGION_LABEL[region]}</span></h3>
            <div className="faint" style={{ fontSize: 12, marginTop: 3 }}>
              资源状态:{created ? <b className="b-teal">已创建 ✓(按需 ASG 存在)</b>
                : rstat?.state === 'partial' ? <b className="b-amber">部分,需重新创建补齐</b>
                : <b className="faint">未创建</b>}
            </div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>关闭 ✕</button>
        </div>

        {!loaded ? <div className="loading">加载中…</div> : (
          <div className="drawer-body">
            {/* VPC */}
            <div className="section-t">1 · 网络(VPC / 子网)</div>
            <label className="chk" style={{ marginBottom: 8 }}>
              <input type="checkbox" checked={createNew} onChange={(e) => { setCreateNew(e.target.checked); if (e.target.checked) { setVpcId(''); setSelSubnets([]); } }} /> 新建 VPC(自动建 10.30/16 + 每 AZ 公有子网)
            </label>
            {createNew ? (
              <div className="chip">provision 时自动创建 VPC / 子网 / 路由,无需选择</div>
            ) : vpcs === null ? <div className="faint" style={{ fontSize: 13 }}>加载 VPC…</div>
              : vpcs.length === 0 ? <Empty>该区无 VPC,请勾选「新建 VPC」</Empty> : (
                <div className="picklist" style={{ maxHeight: 150 }}>
                  {vpcs.map((v) => (
                    <label key={v.vpc_id} className={`pick-row ${vpcId === v.vpc_id ? 'sel' : ''}`}>
                      <span className="left"><input type="radio" name={`vpc-${region}`} checked={vpcId === v.vpc_id} onChange={() => pickVpc(v.vpc_id)} /><span className="mono">{v.vpc_id}</span></span>
                      <span className="faint mono" style={{ fontSize: 12 }}>{v.cidr}{v.is_default ? ' · default' : ''}</span>
                    </label>
                  ))}
                </div>
              )}
            {!createNew && vpcId && (
              busy === 'subnets' ? <div className="faint" style={{ marginTop: 8 }}>加载子网…</div>
                : subnets.length === 0 ? <Empty>该 VPC 无子网</Empty> : (
                  <>
                    <div className="hint" style={{ marginTop: 10 }}>选子网(建议多 AZ;ALB 会自动每 AZ 取一个)。已选 {selSubnets.length} 个。</div>
                    <div className="picklist" style={{ maxHeight: 150 }}>
                      {subnets.map((sn) => {
                        const on = selSubnets.includes(sn.subnet_id);
                        return (
                          <label key={sn.subnet_id} className={`pick-row ${on ? 'sel' : ''}`}>
                            <span className="left"><input type="checkbox" checked={on} onChange={() => toggleSubnet(sn.subnet_id)} /><span className="mono">{sn.subnet_id}</span></span>
                            <span className="faint mono" style={{ fontSize: 12 }}>{sn.az} · {sn.cidr}</span>
                          </label>
                        );
                      })}
                    </div>
                  </>
                )
            )}

            {/* SG + key */}
            <div className="section-t" style={{ marginTop: 18 }}>2 · 安全组 / 密钥</div>
            <div className="form-grid">
              {!createNew && vpcId && (
                <div className="field"><label>安全组(留空 = 自动创建锁定策略)</label>
                  <select value={sgId} onChange={(e) => { setSgId(e.target.value); setAckOpenSg(false); }}>
                    <option value="">自动创建(推荐 · 无 0.0.0.0/0)</option>
                    {sgs.map((gr) => <option key={gr.sg_id} value={gr.sg_id}>{gr.name} · {gr.sg_id}{gr.open_to_world ? ' ⚠ 含 0.0.0.0/0' : ''}</option>)}
                  </select></div>
              )}
              <div className="field"><label>密钥对(留空 = 无密钥)</label>
                <select value={keyName} onChange={(e) => setKeyName(e.target.value)}>
                  <option value="">无密钥(不注入 SSH key)</option>
                  {keys.map((k) => <option key={k.key_name} value={k.key_name}>{k.key_name}</option>)}
                </select></div>
            </div>
            {sgOpen && (
              <div className="banner" style={{ borderColor: 'rgba(251,191,36,.5)', background: 'rgba(251,191,36,.08)', marginTop: 4 }}>
                <span className="i" style={{ color: 'var(--amber)' }}>⚠</span>
                <div>
                  <div style={{ color: 'var(--amber)', fontWeight: 600 }}>该安全组含 0.0.0.0/0 公网入站</div>
                  <div className="hint" style={{ margin: '4px 0 8px' }}>平台建议仅对 Global Accelerator 开放。仍要使用请二次确认。</div>
                  <label className="chk"><input type="checkbox" checked={ackOpenSg} onChange={(e) => setAckOpenSg(e.target.checked)} /> 我已知晓风险,仍使用</label>
                </div>
              </div>
            )}

            {/* AMI */}
            <div className="section-t" style={{ marginTop: 18 }}>3 · AMI / 模型</div>
            <div className="field"><label>AMI ARN / ID(自打包:模型 + 推理引擎,开机自服务)</label>
              <input placeholder="ami-xxxxxxxx" value={amiArn} onChange={(e) => setAmiArn(e.target.value)} /></div>
            <div className="inline">
              <div className="field" style={{ flex: 1 }}><label>服务端口</label>
                <input type="number" value={servingPort} onChange={(e) => setServingPort(Number(e.target.value))} /></div>
              <div className="field" style={{ flex: 1 }}><label>健康检查路径</label>
                <input value={healthPath} onChange={(e) => setHealthPath(e.target.value)} /></div>
            </div>
            <label className="chk"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> 启用该区</label>
            {enabled && !amiArn && <div className="hint" style={{ color: 'var(--amber)', marginTop: 4 }}>需填 AMI 才能启用。</div>}

            {steps.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div className="section-t" style={{ margin: '0 0 8px' }}>创建进度</div>
                <div className="picklist" style={{ maxHeight: 200 }}>
                  {steps.map((st: any, i: number) => (
                    <div key={i} className="chip" style={{ display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{JSON.stringify(st)}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="drawer-foot">
          <button className="btn btn-sm btn-ghost" onClick={save} disabled={!!busy || blockOpen}>{busy === 'save' ? '暂存中…' : '暂存'}</button>
          <button className="btn btn-sm" onClick={provision} disabled={!!busy || !amiArn || blockOpen}>
            {busy === 'provision' ? '创建中…' : created ? '重新创建 / 补齐' : '创建数据面资源'}
          </button>
          {toast && <span className={`toast ${toast.ok ? 'ok' : 'err'}`}>{toast.msg}</span>}
        </div>
      </div>
    </div>
  );
}
