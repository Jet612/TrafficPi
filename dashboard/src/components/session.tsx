"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ApiError, api, onUnauthorized } from "@/lib/api";

export type SessionStatus = "loading" | "anonymous" | "authenticated" | "unreachable";

interface Session {
  status: SessionStatus;
  username: string | null;
  /** Whether a user exists at all; false means the password has not been set yet. */
  configured: boolean | null;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Ask the server again whether this session is still good. */
  recheck: () => void;
}

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession needs a SessionProvider above it");
  return session;
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{ status: SessionStatus; username: string | null; configured: boolean | null }>({
    status: "loading",
    username: null,
    configured: null,
  });

  const recheck = useCallback(() => {
    api
      .me()
      .then(({ username }) => setState((s) => ({ ...s, status: "authenticated", username })))
      .catch(async (error: unknown) => {
        if (error instanceof ApiError && error.status === 401) {
          const auth = await api.authState().catch(() => null);
          setState({ status: "anonymous", username: null, configured: auth?.configured ?? null });
        } else {
          setState((s) => ({ ...s, status: "unreachable" }));
        }
      });
  }, []);

  useEffect(() => {
    // Ask the server who we are once, when the page opens.
    recheck();
    return onUnauthorized(() => setState((s) => ({ ...s, status: "anonymous", username: null })));
  }, [recheck]);

  const signIn = useCallback(async (username: string, password: string) => {
    await api.login(username, password);
    const { username: name } = await api.me();
    setState((s) => ({ ...s, status: "authenticated", username: name }));
  }, []);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setState((s) => ({ ...s, status: "anonymous", username: null }));
  }, []);

  const value = useMemo<Session>(() => ({ ...state, signIn, signOut, recheck }), [state, signIn, signOut, recheck]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
