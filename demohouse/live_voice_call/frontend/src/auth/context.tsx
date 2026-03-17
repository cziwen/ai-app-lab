import {
  createContext,
  type Dispatch,
  type MutableRefObject,
  type PropsWithChildren,
  type SetStateAction,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation } from '@modern-js/runtime/router';
import type { PermissionState } from '@/auth/types';

type MediaState = {
  userMedia: MediaStream | null;
  displayMedia: MediaStream | null;
};

type SessionAuthContextType = {
  token: string | null;
  tokenPresent: boolean;
  checkInPassed: boolean;
  setCheckInPassed: Dispatch<SetStateAction<boolean>>;
  permissions: PermissionState;
  setPermissions: Dispatch<SetStateAction<PermissionState>>;
  mediaStreamsRef: MutableRefObject<MediaState>;
};

const DEFAULT_PERMISSIONS: PermissionState = {
  mic: 'pending',
  camera: 'pending',
  screen: 'pending',
  speaker: 'pending',
};

const SessionAuthContext = createContext<SessionAuthContextType>(
  {} as SessionAuthContextType,
);

export const SessionAuthProvider = ({ children }: PropsWithChildren) => {
  const location = useLocation();
  const [checkInPassed, setCheckInPassed] = useState(false);
  const [permissions, setPermissions] = useState<PermissionState>({
    ...DEFAULT_PERMISSIONS,
  });
  const [token, setToken] = useState<string | null>(null);
  const mediaStreamsRef = useRef<MediaState>({
    userMedia: null,
    displayMedia: null,
  });

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const nextToken = params.get('token');
    const normalizedToken = nextToken?.trim() ? nextToken.trim() : null;
    if (normalizedToken !== token) {
      setCheckInPassed(false);
      setPermissions({ ...DEFAULT_PERMISSIONS });
      mediaStreamsRef.current = {
        userMedia: null,
        displayMedia: null,
      };
      setToken(normalizedToken);
    }
  }, [location.search, token]);

  const value = useMemo(
    () => ({
      token,
      tokenPresent: !!token,
      checkInPassed,
      setCheckInPassed,
      permissions,
      setPermissions,
      mediaStreamsRef,
    }),
    [token, checkInPassed, permissions],
  );

  return (
    <SessionAuthContext.Provider value={value}>
      {children}
    </SessionAuthContext.Provider>
  );
};

export const useSessionAuth = () => {
  return useContext(SessionAuthContext);
};
