import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import { api } from "../api/client";
import type { UserProfile } from "../api/types";
import { clearStoredToken, readStoredToken, writeStoredToken } from "./storage";

type AuthMode = "login" | "register";

type AuthContextValue = {
  isBootstrapping: boolean;
  token: string | null;
  user: UserProfile | null;
  authenticate: (mode: AuthMode, payload: { username: string; password: string }) => Promise<void>;
  refreshProfile: () => Promise<UserProfile | null>;
  setUser: (user: UserProfile | null) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: PropsWithChildren) {
  const [token, setToken] = useState<string | null>(() =>
    typeof window === "undefined" ? null : readStoredToken(),
  );
  const [user, setUser] = useState<UserProfile | null>(null);
  const [isBootstrapping, setIsBootstrapping] = useState(true);

  const refreshProfile = useCallback(async () => {
    if (!token) {
      setUser(null);
      return null;
    }

    try {
      const profile = await api.getMe(token);
      setUser(profile);
      return profile;
    } catch {
      clearStoredToken();
      setToken(null);
      setUser(null);
      return null;
    }
  }, [token]);

  useEffect(() => {
    let isMounted = true;

    async function bootstrap() {
      if (!token) {
        if (isMounted) {
          setUser(null);
          setIsBootstrapping(false);
        }
        return;
      }

      await refreshProfile();
      if (isMounted) {
        setIsBootstrapping(false);
      }
    }

    void bootstrap();

    return () => {
      isMounted = false;
    };
  }, [refreshProfile, token]);

  const authenticate = useCallback(
    async (mode: AuthMode, payload: { username: string; password: string }) => {
      const result = mode === "login" ? await api.login(payload) : await api.register(payload);
      writeStoredToken(result.access_token);
      setToken(result.access_token);
      const profile = await api.getMe(result.access_token);
      setUser(profile);
      setIsBootstrapping(false);
    },
    [],
  );

  const logout = useCallback(() => {
    clearStoredToken();
    setToken(null);
    setUser(null);
    setIsBootstrapping(false);
  }, []);

  const value = useMemo(
    () => ({
      isBootstrapping,
      token,
      user,
      authenticate,
      refreshProfile,
      setUser,
      logout,
    }),
    [authenticate, isBootstrapping, logout, refreshProfile, token, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return context;
}
