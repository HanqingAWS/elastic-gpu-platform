import { useEffect, useRef, useState } from 'react';

export const REGION_LABEL: Record<string, string> = {
  'us-east-1': '弗吉尼亚 · 优先', 'us-east-2': '俄亥俄', 'us-west-2': '俄勒冈',
};
export const REGIONS = ['us-east-1', 'us-east-2', 'us-west-2'];

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
