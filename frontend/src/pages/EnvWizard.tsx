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
  const [nr, setNr] = useState({ region: '', label: '', priority: '' });  // 新增区域表单
  const [adding, setAdding] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [net, cfg, ga] = await Promise.all([
        api.network().catch(() => ({ selections: [] })),
        api.getConfig().catch(() => ({})),
        api.ga().catch(() => null),
      ]);
      const sels = net.selections || [];
      const cfgRegions = cfg.regions || {};
      // 区域列表来自 Config 注册表(含未启用),空则回退硬编码基线
      const rlist = Object.keys(cfgRegions).length ? Object.keys(cfgRegions) : REGIONS;
      const status: Record<string, any> = {};
      await Promise.all(rlist.map(async (r) => {
        const sel = sels.find((x: any) => x.region === r);
        status[r] = await api.regionStatus(r, sel?.vpc_id || undefined).catch(() => null);
      }));
      if (alive) setG({ net: sels, cfg: cfgRegions, ga, status });
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

  // 区域列表 = Config 注册表 keys,按 priority 升序;标签/优先区从注册表派生(回退硬编码常量)
  const regionList: string[] = g && Object.keys(g.cfg).length
    ? Object.keys(g.cfg).sort((a, b) =>
        (Number(g.cfg[a]?.priority ?? 99) - Number(g.cfg[b]?.priority ?? 99)) || a.localeCompare(b))
    : REGIONS;
  const labelOf = (r: string) => (g?.cfg?.[r]?.label) || REGION_LABEL[r] || r;
  const priorityRegion = regionList[0];  // 已按 priority 排序,首个即最高优先
  const anyGreen = g ? regionList.some((r) => stateOf(r) === 'green') : false;

  const addRegion = async () => {
    const region = nr.region.trim();
    if (!region) return;
    setAdding('add');
    try {
      await api.putConfig({ regions: { [region]: {
        label: nr.label.trim() || region,
        priority: nr.priority.trim() ? Number(nr.priority) : 99,
        enabled: false,  // 先加进注册表,设 AMI/网络并 provision 后再启用
      } } });
      setNr({ region: '', label: '', priority: '' });
      setTick((t) => t + 1);
      setOpen(region);  // 直接打开该区抽屉配置
    } catch { /* toast 在抽屉内;此处静默 */ }
    finally { setAdding(null); }
  };

  return (
    <>
      <Banner>
        点任一<b>区域节点</b>配置该区。颜色即状态:
        <b style={{ color: 'var(--faint)' }}> 灰=未配置</b> →
        <b style={{ color: 'var(--blue)' }}> 蓝=已配置·待创建</b> →
        <b style={{ color: 'var(--teal)' }}> 绿=已创建(ASG 存在)</b>。
        链路 <b>Global Accelerator → 各区 ALB → GPU ASG(按需)</b>;台数不在此设置,去「定时活动」配置。
      </Banner>

      {/* 新增区域:加进注册表(默认未启用)→ 打开抽屉配 AMI/网络 → 创建资源(运行时自动建 GA endpoint group)→ 启用 */}
      <div className="card" style={{ padding: 12, marginBottom: 14 }}>
        <div className="inline" style={{ gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="field" style={{ flex: '1 1 160px', margin: 0 }}><label>新增区域(AWS 区域码)</label>
            <input placeholder="如 eu-central-1" value={nr.region} onChange={(e) => setNr({ ...nr, region: e.target.value })} /></div>
          <div className="field" style={{ flex: '1 1 120px', margin: 0 }}><label>显示名</label>
            <input placeholder="如 法兰克福" value={nr.label} onChange={(e) => setNr({ ...nr, label: e.target.value })} /></div>
          <div className="field" style={{ flex: '0 0 100px', margin: 0 }}><label>优先级(小=优先)</label>
            <input type="number" placeholder="99" value={nr.priority} onChange={(e) => setNr({ ...nr, priority: e.target.value })} /></div>
          <button className="btn btn-sm" onClick={addRegion} disabled={!nr.region.trim() || !!adding}>{adding ? '添加中…' : '添加区域'}</button>
        </div>
        <div className="hint" style={{ marginTop: 6 }}>加区无需 cdk deploy —— 创建数据面资源时会运行时新建该区 GA endpoint group。新区默认未启用,配好并创建后在抽屉里勾选「启用该区」。</div>
      </div>

      <GlobalTypes />

      <div className="topo">
        {/* GA 根节点 */}
        <div className={`tnode tn-ga ${anyGreen ? 'tn-green' : ''}`}>
          <div className="tn-t">Global Accelerator</div>
          <div className="tn-s mono">{g?.ga?.accelerator?.dns_name || '—'}</div>
          {g?.ga?.accelerator?.static_ips?.length
            ? <div className="tn-s mono faint">{g.ga.accelerator.static_ips.join('  ·  ')}</div> : null}
        </div>
        <div className="topo-stem" />
        <div className="topo-bus" />

        {/* 三区列 */}
        <div className="topo-cols">
          {regionList.map((r) => {
            const s = stateOf(r);
            const st = g?.status?.[r];
            const preferred = r === priorityRegion;
            const disabled = g?.cfg?.[r]?.enabled === false;
            return (
              <div className="topo-col" key={r}>
                <div className="topo-drop" />
                <div className={`tnode clickable tn-${s}`} onClick={() => setOpen(r)} title="点击配置该区">
                  <div className="tn-t">{r} {preferred ? <span className="star">★</span> : null}{disabled ? <span className="faint" style={{ fontSize: 11 }}> · 未启用</span> : null}</div>
                  <div className="tn-s faint">{labelOf(r)} · ALB</div>
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

// ---------- 全局机型优先级(全按需 · 顺序即优先级;各区可在抽屉里单独覆盖)----------
function GlobalTypes() {
  const [types, setTypes] = useState('');
  const [saved, setSaved] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; t: string } | null>(null);
  useEffect(() => {
    api.getConfig().then((c) => {
      const t = (c.instance_type_priority || ['p4d.24xlarge', 'p4de.24xlarge']).join(', ');
      setTypes(t); setSaved(t);
    }).catch(() => { /* */ });
  }, []);
  const norm = (s: string) => s.replace(/\s/g, '');
  const save = async () => {
    const arr = types.split(',').map((x) => x.trim()).filter(Boolean);
    if (!arr.length) { setMsg({ ok: false, t: '至少填一个机型' }); return; }
    setBusy(true);
    try {
      await api.putConfig({ instance_type_priority: arr });
      setSaved(arr.join(', ')); setTypes(arr.join(', '));
      setMsg({ ok: true, t: '已保存 ✓(对已建区域:去该区抽屉重跑「创建资源」生效)' });
    } catch (e: any) { setMsg({ ok: false, t: e.message }); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ padding: 12, marginBottom: 14 }}>
      <div className="section-t" style={{ margin: '0 0 8px' }}>全局机型优先级 <span className="faint" style={{ fontWeight: 400, fontSize: 12 }}>全按需 · 顺序即优先级(排前先开,开不出用下一个)· 各区可在抽屉里单独覆盖</span></div>
      {/* 预设:两个互斥的顺序 —— 用 subnav 样式(.subnav button.on 才有高亮;.btn 上的 .on 无样式) */}
      <div className="subnav" style={{ marginBottom: 8 }}>
        <button className={norm(types) === 'p4d.24xlarge,p4de.24xlarge' ? 'on' : ''} onClick={() => setTypes('p4d.24xlarge, p4de.24xlarge')}>p4d 优先(默认)</button>
        <button className={norm(types) === 'p4de.24xlarge,p4d.24xlarge' ? 'on' : ''} onClick={() => setTypes('p4de.24xlarge, p4d.24xlarge')}>p4de 优先</button>
      </div>
      <div className="inline" style={{ gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div className="field" style={{ flex: '1 1 260px', margin: 0 }}>
          <input value={types} onChange={(e) => setTypes(e.target.value)} placeholder="p4d.24xlarge, p4de.24xlarge" /></div>
        <button className="btn btn-sm" onClick={save} disabled={busy || types === saved}>{busy ? '保存中…' : '保存'}</button>
        {msg && <span className={`toast ${msg.ok ? 'ok' : 'err'}`}>{msg.t}</span>}
      </div>
    </div>
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
  const [label, setLabel] = useState('');
  const [priority, setPriority] = useState<number>(99);
  const [instTypes, setInstTypes] = useState('');   // 按区机型覆盖(逗号分隔;空=继承全局)
  const [busy, setBusy] = useState<string | null>(null);
  const [steps, setSteps] = useState<any[]>([]);
  const [rstat, setRstat] = useState<any>(null);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);
  const [mode, setMode] = useState<'auto' | 'byo'>('byo');   // 默认 BYO(用现有公网 ALB + 选 GA)
  const [albs, setAlbs] = useState<any[]>([]);
  const [albArn, setAlbArn] = useState('');
  const [accels, setAccels] = useState<any[]>([]);
  const [gaArn, setGaArn] = useState('');                    // '' = 平台默认 GA
  const [checks, setChecks] = useState<any[] | null>(null);  // 校验结果

  useEffect(() => {
    (async () => {
      const [net, cfg, kp, vp, ac] = await Promise.all([
        api.network().catch(() => ({ selections: [] })), api.getConfig().catch(() => ({})),
        api.keyPairs(region).catch(() => ({ key_pairs: [] })), api.vpcs(region).catch(() => ({ vpcs: null })),
        api.accelerators().catch(() => ({ accelerators: [] })),
      ]);
      setKeys(kp.key_pairs || []);
      if (vp.vpcs) setVpcs(vp.vpcs);
      setAccels(ac.accelerators || []);
      const sel = (net.selections || []).find((x: any) => x.region === region);
      if (sel) {
        setCreateNew(!!sel.create_new); setVpcId(sel.vpc_id || '');
        setSgId(sel.sg_id || ''); setKeyName(sel.key_name || '');
        if (sel.mode) setMode(sel.mode);
        setAlbArn(sel.alb_arn || ''); setGaArn(sel.ga_accelerator_arn || '');
        const stored = (((sel.mode === 'byo' ? sel.asg_subnet_ids : sel.subnet_ids) || sel.subnet_ids || []) as string[]);
        if (sel.vpc_id && !sel.create_new) {
          try {
            const [gr, s, al] = await Promise.all([
              api.securityGroups(region, sel.vpc_id), api.subnets(region, sel.vpc_id), api.albs(region, sel.vpc_id)]);
            setSgs(gr.security_groups || []); setSubnets(s.subnets || []); setAlbs(al.albs || []);
            // 只保留仍存在的子网:记录里已删除的子网界面看不到、也取消不掉,若带进 provision 会报 InvalidSubnet
            const availIds = new Set((s.subnets || []).map((x: any) => x.subnet_id));
            setSelSubnets(stored.filter((id) => availIds.has(id)));
          } catch { setSelSubnets(stored); }
        } else {
          setSelSubnets(stored);
        }
      }
      const rc = (cfg.regions || {})[region] || {};
      setAmiArn(rc.ami_arn || ''); setServingPort(rc.serving_port || 8000);
      setHealthPath(rc.health_path || '/health'); setEnabled(rc.enabled ?? true);
      setLabel(rc.label || REGION_LABEL[region] || ''); setPriority(rc.priority ?? 99);
      setInstTypes((rc.instance_types || []).join(', '));
      setLoaded(true);
      api.regionStatus(region, sel?.vpc_id || undefined).then(setRstat).catch(() => {});
    })();
  }, [region]);

  const pickVpc = async (id: string) => {
    setVpcId(id); setCreateNew(false); setSelSubnets([]); setSgId(''); setAlbArn(''); setBusy('subnets'); setSubnets([]); setSgs([]); setAlbs([]);
    try {
      const r = await api.subnets(region, id); setSubnets(r.subnets || []);
      const gr = await api.securityGroups(region, id); setSgs(gr.security_groups || []);
      const al = await api.albs(region, id); setAlbs(al.albs || []);   // BYO:选 VPC 后自动列该 VPC 的 ALB
    } catch (e: any) { setToast({ ok: false, msg: e.message }); }
    finally { setBusy(null); }
  };
  const toggleSubnet = (id: string) =>
    setSelSubnets((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  // 只用仍存在的子网(以当前有效选择为准,过滤掉记录里已删除的;子网列表未加载时不过滤,避免误清)
  const validSubnets = (): string[] => {
    if (!subnets.length) return selSubnets;
    const availIds = new Set(subnets.map((x: any) => x.subnet_id));
    return selSubnets.filter((id) => availIds.has(id));
  };

  const saveAll = async (): Promise<boolean> => {
    setToast(null);
    const sel = validSubnets();
    try {
      await api.putNetwork({
        region, mode, vpc_id: createNew ? null : vpcId, create_new: createNew && mode === 'auto',
        subnet_ids: mode === 'byo' ? [] : (createNew ? [] : sel),
        asg_subnet_ids: mode === 'byo' ? sel : [],
        alb_arn: mode === 'byo' ? (albArn || null) : null,
        ga_accelerator_arn: gaArn || null,
        sg_id: createNew && mode === 'auto' ? null : (sgId || null), key_name: keyName || null,
      });
      await api.putConfig({ regions: { [region]: {
        ami_arn: amiArn, serving_port: Number(servingPort), health_path: healthPath, enabled: enabled && !!amiArn,
        label: label.trim() || region, priority: Number(priority),
        instance_types: instTypes.split(',').map((x) => x.trim()).filter(Boolean),  // 空数组=继承全局
      } } });
      return true;
    } catch (e: any) { setToast({ ok: false, msg: e.message }); return false; }
  };
  const save = async () => { setBusy('save'); if (await saveAll()) { setToast({ ok: true, msg: '配置已保存 ✓' }); onChanged(); } setBusy(null); };

  const validate = async () => {
    setBusy('validate'); setChecks(null); setToast(null);
    try {
      const r = await api.validate({ region, alb_arn: albArn, ga_accelerator_arn: gaArn || null,
        asg_subnet_ids: selSubnets, node_sg_id: sgId || null, serving_port: Number(servingPort) });
      setChecks(r.checks || []);
    } catch (e: any) { setToast({ ok: false, msg: e.message }); }
    finally { setBusy(null); }
  };
  const newGa = async () => {
    const name = window.prompt('新建 GA 名称:', `nlp-byo-${region}`);
    if (!name) return;
    setBusy('newga');
    try { const r = await api.createAccelerator(name.trim()); setGaArn(r.accelerator_arn); const ac = await api.accelerators(); setAccels(ac.accelerators || []); setToast({ ok: true, msg: `已新建 GA ${r.accelerator_arn}` }); }
    catch (e: any) { setToast({ ok: false, msg: e.message }); } finally { setBusy(null); }
  };

  const removeRegion = async () => {
    if (!window.confirm(`移除 ${region}?\n\n将从注册表删除,并在后台拆除该区 AWS 资源:GA endpoint group / ALB / TargetGroup / ASG / 启动模板。\n保留 VPC / 子网 / 安全组(可复用;之后可重新添加并创建资源)。\n拆除约需 1–2 分钟,Global Accelerator 页稍后会更新。`)) return;
    setBusy('remove');
    try { await api.deleteRegion(region); onChanged(); onClose(); }
    catch (e: any) { setToast({ ok: false, msg: e.message }); setBusy(null); }
  };

  const provision = async () => {
    if (!gaArn) { setToast({ ok: false, msg: '请先选择或新建 GA(平台需把该区 ALB 注册进 GA)' }); return; }
    setBusy('provision'); setSteps([]);
    if (!(await saveAll())) { setBusy(null); return; }
    setToast({ ok: true, msg: '已提交,后台创建中(约 1–3 分钟,可留在本页看进度)…' });
    try {
      const common = { region, ami_id: amiArn, sg_id: sgId || null, key_name: keyName || null,
        ga_accelerator_arn: gaArn || null, serving_port: Number(servingPort), health_path: healthPath,
        metrics_port: Number(servingPort), dry_run: false };
      const sel = validSubnets();
      const body = mode === 'byo'
        ? { ...common, mode: 'byo', vpc_id: vpcId || null, alb_arn: albArn || null, asg_subnet_ids: sel }
        : { ...common, mode: 'auto', vpc_id: createNew ? null : (vpcId || null),
            subnet_ids: createNew ? null : (sel.length ? sel : null),
            sg_id: createNew ? null : (sgId || null) };
      const { run_id } = await api.provision(body);
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

  // 只更新 AMI:仅给该区 LT 追加新版本(换 ImageId)+ 写库,不碰子网/安全组/ALB/GA。已建成的区才有意义。
  const updateAmiOnly = async () => {
    if (!amiArn) { setToast({ ok: false, msg: '请先填 AMI' }); return; }
    // 确认框:确定=更新,取消=不更新(简明,不堆术语)
    if (!window.confirm(`更新 ${region} 的 AMI 为:\n${amiArn}\n\n下次拉起新实例时生效。确定?`)) return;
    setBusy('ami'); setToast(null);
    try {
      const r = await api.updateAmi(region, amiArn, false);  // 不滚动替换在跑实例(纯更模板 + 写库)
      setToast({ ok: true, msg: `AMI 已更新 → 启动模板 v${r.launch_template?.new_version}(新实例生效;在跑实例不变)✓` });
      onChanged();
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
            <h3 style={{ margin: 0 }}>{region} <span className="faint" style={{ fontWeight: 400, fontSize: 13 }}>· {label || REGION_LABEL[region] || region}</span></h3>
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
            {/* 模式:BYO(默认)/ auto */}
            <div className="subnav" style={{ marginBottom: 12 }}>
              <button className={mode === 'byo' ? 'on' : ''} onClick={() => setMode('byo')}>BYO:现有公网 ALB / GA(默认)</button>
              <button className={mode === 'auto' ? 'on' : ''} onClick={() => { setMode('auto'); setAlbArn(''); }}>平台自动创建</button>
            </div>
            {mode === 'byo' && <div className="hint" style={{ marginBottom: 8 }}>自己在公有子网建好 internet-facing ALB;这里选它 + 选 GA,并给 ASG 选<b>私有子网</b>。平台建 TG/监听器/ASG 并校验,不建 ALB/VPC。</div>}
            {/* VPC */}
            <div className="section-t">1 · 网络(VPC / 子网)</div>
            {mode === 'auto' && (
              <label className="chk" style={{ marginBottom: 8 }}>
                <input type="checkbox" checked={createNew} onChange={(e) => { setCreateNew(e.target.checked); if (e.target.checked) { setVpcId(''); setSelSubnets([]); } }} /> 新建 VPC(自动建 10.30/16 + 每 AZ 公有子网)
              </label>
            )}
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
                    <div className="hint" style={{ marginTop: 10 }}>
                      {mode === 'byo' ? <>给 ASG(GPU 实例)选<b>私有子网</b>(标 🔒);实例走私有子网,需 VPC 有 NAT/VPC endpoint 出网。</> : '选子网(建议多 AZ;ALB 会自动每 AZ 取一个)。'} 已选 {selSubnets.length} 个。
                    </div>
                    <div className="picklist" style={{ maxHeight: 150 }}>
                      {subnets.map((sn) => {
                        const on = selSubnets.includes(sn.subnet_id);
                        return (
                          <label key={sn.subnet_id} className={`pick-row ${on ? 'sel' : ''}`}>
                            <span className="left"><input type="checkbox" checked={on} onChange={() => toggleSubnet(sn.subnet_id)} /><span className="mono">{sn.subnet_id}</span>
                              <span className="tag" style={{ marginLeft: 6, background: sn.public ? 'rgba(251,191,36,.12)' : 'rgba(52,211,153,.12)', color: sn.public ? 'var(--amber)' : 'var(--teal)', border: 'none' }}>{sn.public ? '公有' : `私有·${sn.egress === 'nat' ? 'NAT' : '无出网'}`}</span></span>
                            <span className="faint mono" style={{ fontSize: 12 }}>{sn.az} · {sn.cidr}</span>
                          </label>
                        );
                      })}
                    </div>
                  </>
                )
            )}

            {/* 1b · BYO:现有公网 ALB + GA */}
            {mode === 'byo' && !createNew && vpcId && (
              <>
                <div className="section-t" style={{ marginTop: 18 }}>1b · 现有公网 ALB(BYO)</div>
                <div className="field"><label>公网 ALB(选 VPC 后自动列出该 VPC 内的 ALB)</label>
                  <select value={albArn} onChange={(e) => setAlbArn(e.target.value)}>
                    <option value="">— 选择 ALB —</option>
                    {albs.map((a) => <option key={a.alb_arn} value={a.alb_arn}>{a.name} · {a.scheme}{a.scheme !== 'internet-facing' ? ' ⚠非公网' : ''}</option>)}
                  </select></div>
                {albArn && albs.find((a) => a.alb_arn === albArn && a.scheme !== 'internet-facing') && (
                  <div className="hint" style={{ color: 'var(--amber)' }}>该 ALB 非 internet-facing,请改用公网 ALB。</div>)}
              </>
            )}

            {/* GA(auto + byo 都需要:平台把该区 ALB 注册进所选 GA。CDK 不再建平台 GA,故必须显式选/新建) */}
            <div className="field" style={{ marginTop: 14 }}><label>Global Accelerator(必选:平台把该区 ALB 注册进此 GA)</label>
              <div className="inline">
                <select value={gaArn} onChange={(e) => setGaArn(e.target.value)} style={{ flex: 1 }}>
                  <option value="">— 请选择 GA(或新建) —</option>
                  {accels.map((g) => <option key={g.arn} value={g.arn}>{g.name} · {g.dns}</option>)}
                </select>
                <button className="btn btn-sm btn-ghost" onClick={newGa} disabled={!!busy} style={{ width: 'auto' }}>{busy === 'newga' ? '新建中…' : '+ 新建 GA'}</button>
              </div>
              {!gaArn && <div className="hint" style={{ color: 'var(--amber)' }}>创建资源前需选择或新建一个 GA。</div>}
            </div>

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

            {/* 区域元数据:显示名 / 优先级 / 按区机型覆盖 */}
            <div className="section-t" style={{ marginTop: 18 }}>4 · 区域(显示名 / 优先级 / 机型)</div>
            <div className="inline">
              <div className="field" style={{ flex: 1 }}><label>显示名</label>
                <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={region} /></div>
              <div className="field" style={{ flex: 1 }}><label>优先级(小=优先拉起,0 最高)</label>
                <input type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} /></div>
            </div>
            <div className="field"><label>机型优先级 · 按区覆盖(逗号分隔,顺序即优先级;<b>留空=继承全局</b>)</label>
              <input value={instTypes} onChange={(e) => setInstTypes(e.target.value)} placeholder="留空继承全局(如 p4d.24xlarge, p4de.24xlarge)" /></div>
            <div className="hint">该区不提供所填机型时,创建资源会报错;系统会按该区实际供给过滤(如 EU 无 p4de 自动跳过)。</div>

            {/* 5 · 校验(BYO):GA→ALB 连通 / 安全组 / 私有子网出网 */}
            {mode === 'byo' && (
              <>
                <div className="section-t" style={{ marginTop: 18 }}>5 · 校验(GA→ALB / 安全组 / 私有子网出网)</div>
                <div className="hint" style={{ margin: '0 0 6px' }}>建议 provision 前先校验。安全组缺口自动补(不重复);其余风险点(私有子网无出网、ALB 非公网、监听器冲突)按提示手动改。</div>
                <button className="btn btn-sm btn-ghost" onClick={validate} disabled={!!busy || !albArn} style={{ width: 'auto' }}>{busy === 'validate' ? '校验中…' : '运行校验'}</button>
                {checks && (
                  <div className="picklist" style={{ maxHeight: 240, marginTop: 8 }}>
                    {checks.length === 0 ? <div className="chip">无检查项</div> : checks.map((c, i) => (
                      <div key={i} className="pick-row" style={{ alignItems: 'flex-start' }}>
                        <span className="left"><span style={{ width: 20 }}>{c.status === 'ok' ? '✅' : c.status === 'fixed' ? '🔧' : c.status === 'warn' ? '⚠️' : '❌'}</span><span className="mono">{c.name}</span></span>
                        <span className="faint" style={{ fontSize: 12, textAlign: 'right', maxWidth: '62%' }}>{c.detail}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

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
          <button className="btn btn-sm btn-ghost" onClick={removeRegion} disabled={!!busy} title="移除并拆除该区 GA/ALB/ASG(保留 VPC 可复用)" style={{ color: 'var(--rose, #f43f5e)', marginRight: 'auto' }}>{busy === 'remove' ? '移除中…' : '移除区域'}</button>
          <button className="btn btn-sm btn-ghost" onClick={save} disabled={!!busy || blockOpen}>{busy === 'save' ? '暂存中…' : '暂存'}</button>
          {created && (
            <button className="btn btn-sm btn-ghost" onClick={updateAmiOnly} disabled={!!busy || !amiArn}
              title="只把该区 AMI 换成上面填的 ARN(启动模板追加新版本 + 写库),不碰网络 / 安全组 / ALB / GA">
              {busy === 'ami' ? '更新中…' : '只更新 AMI'}
            </button>
          )}
          <button className="btn btn-sm" onClick={provision} disabled={!!busy || !amiArn || blockOpen || (mode === 'byo' && (!albArn || !vpcId || selSubnets.length === 0))}>
            {busy === 'provision' ? '创建中…' : created ? '重新创建 / 补齐' : '创建数据面资源'}
          </button>
          {toast && <span className={`toast ${toast.ok ? 'ok' : 'err'}`}>{toast.msg}</span>}
        </div>
      </div>
    </div>
  );
}
