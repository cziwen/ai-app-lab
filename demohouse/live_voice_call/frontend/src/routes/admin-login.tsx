import { FormEvent, useState } from 'react';
import { useNavigate } from '@modern-js/runtime/router';
import { adminApi } from '@/admin/api';

export const AdminLoginPage = () => {
  const navigate = useNavigate();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setLoading(true);
    try {
      await adminApi.login(username.trim(), password);
      navigate('/admin/jobs');
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="admin-login-page">
      <section className="admin-login-card">
        <h1>管理员登录</h1>
        <form onSubmit={handleSubmit}>
          <label htmlFor="admin-username">账号</label>
          <input
            id="admin-username"
            value={username}
            onChange={event => setUsername(event.target.value)}
            placeholder="请输入账号"
          />
          <label htmlFor="admin-password">密码</label>
          <input
            id="admin-password"
            type="password"
            value={password}
            onChange={event => setPassword(event.target.value)}
            placeholder="请输入密码"
          />
          {error && <p className="admin-error">{error}</p>}
          <button type="submit" disabled={loading || !username.trim() || !password}>
            {loading ? '登录中...' : '登录'}
          </button>
        </form>
      </section>
    </main>
  );
};
