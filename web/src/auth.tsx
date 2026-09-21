import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, tokenStore, Unauthorized, type Me } from "./api";

interface AuthValue {
  me: Me | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => void;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!tokenStore.get()) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then(setMe)
      .catch((err) => {
        // token 過期或 Observ 拒絕：清掉，讓使用者重新登入。
        if (err instanceof Unauthorized) tokenStore.clear();
      })
      .finally(() => setLoading(false));
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    await api.login(email, password);
    setMe(await api.me());
  }, []);

  const signOut = useCallback(() => {
    tokenStore.clear();
    setMe(null);
  }, []);

  return <Ctx.Provider value={{ me, loading, signIn, signOut }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(Ctx);
  if (!value) throw new Error("useAuth 必須在 AuthProvider 內使用");
  return value;
}
