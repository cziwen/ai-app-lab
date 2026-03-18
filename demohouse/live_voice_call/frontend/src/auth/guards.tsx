import { type PropsWithChildren, useEffect, useState } from 'react';
import { Navigate, useLocation } from '@modern-js/runtime/router';
import { useSessionAuth } from '@/auth/context';
import { API_URL } from '@/config/endpoints';
import { InvalidLinkPage } from '@/routes/invalid-link';

const isLocalHost = () => {
  if (typeof window === 'undefined') {
    return false;
  }
  return (
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1' ||
    window.location.hostname === '::1'
  );
};

const isSessionAllowed = (search: string) => {
  const params = new URLSearchParams(search);
  const session = params.get('session');
  if (session !== 's1') {
    return true;
  }
  return isLocalHost();
};

export const RequireToken = ({ children }: PropsWithChildren) => {
  const location = useLocation();
  const { tokenPresent } = useSessionAuth();
  if (!tokenPresent || !isSessionAllowed(location.search)) {
    return <InvalidLinkPage />;
  }
  return <>{children}</>;
};

const ActiveInterviewTokenGuard = ({
  children,
  requireCheckIn,
}: PropsWithChildren<{ requireCheckIn: boolean }>) => {
  const location = useLocation();
  const { token, tokenPresent, checkInPassed } = useSessionAuth();
  const [validating, setValidating] = useState(true);
  const [validToken, setValidToken] = useState(false);

  useEffect(() => {
    let active = true;
    const run = async () => {
      if (!tokenPresent || !token || !isSessionAllowed(location.search)) {
        if (active) {
          setValidToken(false);
          setValidating(false);
        }
        return;
      }
      setValidating(true);
      try {
        const response = await fetch(
          `${API_URL}/api/public/interviews/${encodeURIComponent(token)}/access`,
        );
        if (!active) {
          return;
        }
        setValidToken(response.ok);
      } catch (_error) {
        if (active) {
          setValidToken(false);
        }
      } finally {
        if (active) {
          setValidating(false);
        }
      }
    };
    run();
    return () => {
      active = false;
    };
  }, [location.search, tokenPresent, token]);

  if (validating) {
    return (
      <main className="gate-page">
        <section className="gate-card">
          <h1>链接校验中</h1>
          <p>正在校验面试链接，请稍候...</p>
        </section>
      </main>
    );
  }

  if (!validToken) {
    return <InvalidLinkPage />;
  }
  if (requireCheckIn && !checkInPassed) {
    return <Navigate replace to={`/check-in${location.search}`} />;
  }
  return <>{children}</>;
};

export const RequireActiveInterviewToken = ({ children }: PropsWithChildren) => {
  return (
    <ActiveInterviewTokenGuard requireCheckIn={false}>
      {children}
    </ActiveInterviewTokenGuard>
  );
};

export const RequireTokenAndCheckIn = ({ children }: PropsWithChildren) => {
  return (
    <ActiveInterviewTokenGuard requireCheckIn>
      {children}
    </ActiveInterviewTokenGuard>
  );
};
