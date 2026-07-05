import { AuthProvider, useAuth } from './hooks/useAuth';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';

function Gate() {
  const { user, ready } = useAuth();
  if (!ready) return <p style={{ fontFamily: 'system-ui', padding: 24 }}>加载中…</p>;
  return user ? <Dashboard /> : <Login />;
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
