import { Amplify } from 'aws-amplify';

let configured = false;

export async function initAmplify(): Promise<boolean> {
  try {
    const r = await fetch('/api/auth/config');
    const cfg = await r.json();
    if (cfg?.configured && cfg.userPoolId && cfg.clientId) {
      Amplify.configure({
        Auth: { Cognito: { userPoolId: cfg.userPoolId, userPoolClientId: cfg.clientId } },
      });
      configured = true;
    }
  } catch (e) {
    console.warn('amplify config fetch failed (dev mode)', e);
  }
  return configured;
}

export function isConfigured() {
  return configured;
}
