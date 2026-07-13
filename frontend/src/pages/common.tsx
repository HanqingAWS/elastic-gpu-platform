import { useEffect, useRef, useState } from 'react';

// 以下三个常量已降级为「回退默认」—— 运行时区域来自 /api/regions(Config 驱动)。
// 仅在接口失败/为空时兜底,或用于离线渲染。真实区域集/标签/优先级请用 normalizeRegions()。
export const REGION_LABEL: Record<string, string> = {
  'eu-north-1': '斯德哥尔摩', 'us-east-1': '弗吉尼亚', 'us-east-2': '俄亥俄', 'us-west-2': '俄勒冈',
};
export const REGIONS = ['us-east-1', 'us-east-2', 'us-west-2', 'eu-north-1'];
export const PRIORITY_REGION = 'eu-north-1';

export type RegionInfo = { region: string; label: string; priority: number; enabled?: boolean; instance_types?: string[] | null };

// 归一化 /api/regions 响应。新版=对象数组 {region,label,priority,enabled,instance_types};
// 旧版/回退=字符串数组或空 → 用硬编码常量兜底。返回排序后的 items + 便捷映射 + 优先区(★)。
export function normalizeRegions(raw: any): {
  items: RegionInfo[]; ids: string[]; label: Record<string, string>; priority: Record<string, number>; priorityRegion: string;
} {
  const arr: any[] = Array.isArray(raw) ? raw : [];
  let items: RegionInfo[];
  if (arr.length && typeof arr[0] === 'object') {
    items = arr.map((r) => ({
      region: r.region, label: r.label || REGION_LABEL[r.region] || r.region,
      priority: typeof r.priority === 'number' ? r.priority : 99,
      enabled: r.enabled, instance_types: r.instance_types ?? null,
    }));
  } else {
    const ids: string[] = arr.length ? arr : REGIONS;
    items = ids.map((r, i) => ({ region: r, label: REGION_LABEL[r] || r, priority: i }));
  }
  items = [...items].sort((a, b) => a.priority - b.priority || a.region.localeCompare(b.region));
  const label: Record<string, string> = {};
  const priority: Record<string, number> = {};
  items.forEach((r) => { label[r.region] = r.label; priority[r.region] = r.priority; });
  return { items, ids: items.map((r) => r.region), label, priority, priorityRegion: items[0]?.region ?? PRIORITY_REGION };
}

export function Copy({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch { /* noop */ } ta.remove();
    }
    setOk(true); setTimeout(() => setOk(false), 1200);
  };
  return (
    <button className={`copy-btn ${ok ? 'ok' : ''}`} onClick={copy} title="复制">
      {ok ? '✓ 已复制' : '⧉ 复制'}
    </button>
  );
}

export const Loading = () => <div className="loading">加载中…</div>;
export const Empty = ({ icon, hint, children }: { icon?: string; hint?: string; children: any }) => (
  <div className="empty">
    {icon ? <span className="ico" aria-hidden="true">{icon}</span> : null}
    {children}
    {hint ? <span className="hint">{hint}</span> : null}
  </div>
);
export const Banner = ({ children }: { children: any }) => (
  <div className="banner"><span className="i">ℹ</span><span>{children}</span></div>
);

export function fmt(ts?: number) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString(); } catch { return String(ts); }
}

export function healthClass(s?: string) {
  const v = (s || '').toLowerCase();
  if (v.includes('healthy') || v === 'inservice') return 'g';
  if (v.includes('unhealthy') || v.includes('terminat')) return 'r';
  if (!v) return 'm';
  return 'a';
}

// 轮询 hook:立即取一次,之后每 ms 刷新;dep 变化会立即重取(用于筛选条件切换)。组件卸载即停。
export function useLive<T>(fn: () => Promise<T>, ms = 15000, dep: any = null): { data: T | null; err: string | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn); fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    const tick = async () => {
      try { const d = await fnRef.current(); if (alive) { setData(d); setErr(null); } }
      catch (e: any) { if (alive) setErr(e?.message ?? String(e)); }
      finally { if (alive) setLoading(false); }
    };
    tick();
    const id = setInterval(tick, ms);
    return () => { alive = false; clearInterval(id); };
  }, [ms, dep]);
  return { data, err, loading };
}
