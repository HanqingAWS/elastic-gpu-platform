import { fetchAuthSession } from 'aws-amplify/auth';
import { isConfigured } from '../config/amplify';

async function token(): Promise<string | null> {
  if (!isConfigured()) return null; // dev 模式无鉴权
  try {
    const s = await fetchAuthSession();
    return s.tokens?.idToken?.toString() ?? null;
  } catch {
    return null;
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(init.headers as any) };
  const t = await token();
  if (t) headers['Authorization'] = `Bearer ${t}`;
  const res = await fetch(`/api${path}`, { ...init, headers });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const api = {
  // config
  getConfig: () => req<any>('/config'),
  putConfig: (body: any) => req<any>('/config', { method: 'PUT', body: JSON.stringify(body) }),
  // fleet / regions
  fleet: () => req<any>('/fleet'),
  regions: () => req<any>('/regions'),
  deleteRegion: (region: string) => req<any>(`/config/regions/${encodeURIComponent(region)}`, { method: 'DELETE' }),
  runs: () => req<any>('/runs'),
  // monitoring
  metrics: () => req<any>('/metrics'),
  spotEvents: () => req<any>('/spot-events'),
  runningHours: (from?: string, to?: string) => {
    const q = new URLSearchParams();
    if (from) q.set('from', from);
    if (to) q.set('to', to);
    const s = q.toString();
    return req<any>(`/running-hours${s ? `?${s}` : ''}`);
  },
  // schedules(定时活动事件)
  getSchedules: () => req<any>('/schedules'),
  putSchedule: (body: any) => req<any>('/schedules', { method: 'PUT', body: JSON.stringify(body) }),
  deleteSchedule: (id: string) => req<any>(`/schedules/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // global accelerator
  ga: () => req<any>('/ga'),
  // networking
  network: () => req<any>('/network'),
  putNetwork: (body: any) => req<any>('/network', { method: 'PUT', body: JSON.stringify(body) }),
  vpcs: (region: string) => req<any>(`/provisioning/vpcs?region=${encodeURIComponent(region)}`),
  subnets: (region: string, vpcId: string) =>
    req<any>(`/provisioning/subnets?region=${encodeURIComponent(region)}&vpc_id=${encodeURIComponent(vpcId)}`),
  securityGroups: (region: string, vpcId: string) =>
    req<any>(`/provisioning/security-groups?region=${encodeURIComponent(region)}&vpc_id=${encodeURIComponent(vpcId)}`),
  keyPairs: (region: string) => req<any>(`/provisioning/key-pairs?region=${encodeURIComponent(region)}`),
  provision: (body: any) => req<any>('/provisioning/provision', { method: 'POST', body: JSON.stringify(body) }),
  provisionStatus: (runId: string) => req<any>(`/provisioning/provision-status?run_id=${encodeURIComponent(runId)}`),
  regionStatus: (region: string, vpcId?: string) =>
    req<any>(`/provisioning/status?region=${encodeURIComponent(region)}${vpcId ? `&vpc_id=${encodeURIComponent(vpcId)}` : ''}`),
  albs: (region: string, vpcId?: string) =>
    req<any>(`/provisioning/albs?region=${encodeURIComponent(region)}${vpcId ? `&vpc_id=${encodeURIComponent(vpcId)}` : ''}`),
  accelerators: () => req<any>('/provisioning/accelerators'),
  createAccelerator: (name: string) => req<any>('/provisioning/accelerator', { method: 'POST', body: JSON.stringify({ name }) }),
  validate: (body: any) => req<any>('/provisioning/validate', { method: 'POST', body: JSON.stringify(body) }),
  // agent
  agentActions: (date?: string) => req<any>(`/agent/actions${date ? `?date=${encodeURIComponent(date)}` : ''}`),
  testModel: (modelId: string) => req<any>('/agent/test-model', { method: 'POST', body: JSON.stringify({ model_id: modelId }) }),
};
