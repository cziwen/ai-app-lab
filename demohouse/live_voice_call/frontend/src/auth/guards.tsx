import type { PropsWithChildren } from 'react';
import { Navigate, useLocation } from '@modern-js/runtime/router';
import { useSessionAuth } from '@/auth/context';
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

export const RequireTokenAndCheckIn = ({ children }: PropsWithChildren) => {
  const location = useLocation();
  const { tokenPresent, checkInPassed } = useSessionAuth();
  if (!tokenPresent || !isSessionAllowed(location.search)) {
    return <InvalidLinkPage />;
  }
  if (!checkInPassed) {
    return <Navigate replace to={`/check-in${location.search}`} />;
  }
  return <>{children}</>;
};
