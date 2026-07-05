import { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { signIn, signUp, confirmSignUp, signOut, getCurrentUser, fetchUserAttributes } from 'aws-amplify/auth';
import { initAmplify, isConfigured } from '../config/amplify';

interface Ctx {
  user: any;
  ready: boolean;
  login: (email: string, pw: string) => Promise<void>;
  register: (email: string, pw: string) => Promise<void>;
  confirm: (email: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthCtx = createContext<Ctx>(null as any);
export const useAuth = () => useContext(AuthCtx);

// 取登录用户,显示邮箱(而非 Cognito sub UUID)
async function loadUser() {
  const cu = await getCurrentUser();
  let email = cu.signInDetails?.loginId as string | undefined;
  if (!email) {
    try { email = (await fetchUserAttributes()).email; } catch { /* ignore */ }
  }
  return { username: email || cu.username, email, sub: cu.userId };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<any>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (async () => {
      await initAmplify();
      if (!isConfigured()) setUser({ username: 'dev-user', devMode: true });
      else { try { setUser(await loadUser()); } catch { /* not logged in */ } }
      setReady(true);
    })();
  }, []);

  const login = async (email: string, pw: string) => {
    if (!isConfigured()) { setUser({ username: 'dev-user', devMode: true }); return; }
    await signIn({ username: email, password: pw, options: { authFlowType: 'USER_PASSWORD_AUTH' } });
    setUser(await loadUser());
  };
  const register = async (email: string, pw: string) => {
    await signUp({ username: email, password: pw, options: { userAttributes: { email } } });
  };
  const confirm = async (email: string, code: string) => {
    await confirmSignUp({ username: email, confirmationCode: code });
  };
  const logout = async () => { if (isConfigured()) await signOut(); setUser(null); };

  return <AuthCtx.Provider value={{ user, ready, login, register, confirm, logout }}>{children}</AuthCtx.Provider>;
}
