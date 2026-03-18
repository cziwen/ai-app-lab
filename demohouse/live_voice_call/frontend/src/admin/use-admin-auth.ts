import { useEffect, useState } from 'react';
import { useNavigate } from '@modern-js/runtime/router';
import { adminApi } from '@/admin/api';

export const useAdminAuth = () => {
  const navigate = useNavigate();
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [username, setUsername] = useState('');
  const [globalError, setGlobalError] = useState('');

  useEffect(() => {
    let active = true;

    const boot = async () => {
      try {
        const me = await adminApi.me();
        if (!active) {
          return;
        }
        setUsername(me.admin.username);
      } catch (_error) {
        if (active) {
          navigate('/admin/login');
        }
        return;
      }

      if (active) {
        setLoadingAuth(false);
      }
    };

    boot();
    return () => {
      active = false;
    };
  }, [navigate]);

  const handleLogout = async () => {
    await adminApi.logout();
    navigate('/admin/login');
  };

  return {
    loadingAuth,
    username,
    globalError,
    setGlobalError,
    handleLogout,
  };
};
