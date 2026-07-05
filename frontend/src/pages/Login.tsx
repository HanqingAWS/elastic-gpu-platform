import { useState } from 'react';
import { useAuth } from '../hooks/useAuth';

type Mode = 'login' | 'register' | 'confirm';

export default function Login() {
  const { login, register, confirm } = useAuth();
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [code, setCode] = useState('');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(''); setMsg(''); setBusy(true);
    try {
      if (mode === 'login') await login(email, pw);
      else if (mode === 'register') { await register(email, pw); setMode('confirm'); setMsg('验证码已发送到邮箱,请查收'); }
      else { await confirm(email, code); setMode('login'); setMsg('邮箱验证成功,请登录'); }
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="brand">
          <div className="logo">N</div>
          <div>
            <h1>NLP-Platform</h1>
            <div className="sub">GPU Orchestration</div>
          </div>
        </div>

        {mode !== 'confirm' && (
          <div className="tabs">
            <button className={mode === 'login' ? 'on' : ''} onClick={() => setMode('login')}>登录</button>
            <button className={mode === 'register' ? 'on' : ''} onClick={() => setMode('register')}>注册</button>
          </div>
        )}

        <form onSubmit={submit}>
          <div className="field">
            <label>邮箱</label>
            <input type="email" placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          {mode !== 'confirm' && (
            <div className="field">
              <label>密码</label>
              <input type="password" placeholder="≥12 位,含大小写、数字、符号" value={pw} onChange={(e) => setPw(e.target.value)} required />
            </div>
          )}
          {mode === 'confirm' && (
            <div className="field">
              <label>邮箱验证码</label>
              <input placeholder="6 位验证码" value={code} onChange={(e) => setCode(e.target.value)} required />
            </div>
          )}
          <button className="btn" type="submit" disabled={busy}>
            {busy ? '处理中…' : mode === 'login' ? '登 录' : mode === 'register' ? '注 册' : '确 认'}
          </button>
        </form>

        {msg && <div className="msg ok">{msg}</div>}
        {err && <div className="msg err">{err}</div>}
      </div>
    </div>
  );
}
