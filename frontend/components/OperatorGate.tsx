"use client";

import { useEffect, useReducer, useState } from "react";

import {
  ApiError,
  getOperatorStatus,
  OPERATOR_AUTH_REQUIRED_EVENT,
  unlockOperator,
} from "@/lib/api";
import {
  initialOperatorAuthState,
  isValidOperatorToken,
  operatorAuthReducer,
} from "@/lib/operatorAuth";

export default function OperatorGate({ children }: Readonly<{ children: React.ReactNode }>) {
  const [state, dispatch] = useReducer(operatorAuthReducer, initialOperatorAuthState);
  const [token, setToken] = useState("");

  useEffect(() => {
    let isActive = true;
    getOperatorStatus()
      .then(({ unlocked }) => {
        if (isActive) dispatch({ type: "checked", isUnlocked: unlocked });
      })
      .catch(() => {
        if (isActive) {
          dispatch({ type: "failed", message: "Cannot reach the backend operator gate." });
        }
      });
    const handleExpiredSession = () => dispatch({ type: "locked" });
    window.addEventListener(OPERATOR_AUTH_REQUIRED_EVENT, handleExpiredSession);
    return () => {
      isActive = false;
      window.removeEventListener(OPERATOR_AUTH_REQUIRED_EVENT, handleExpiredSession);
    };
  }, []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const suppliedToken = token.trim();
    if (!isValidOperatorToken(suppliedToken)) {
      dispatch({ type: "failed", message: "Enter the 32-character-or-longer operator token." });
      return;
    }
    dispatch({ type: "unlocking" });
    try {
      await unlockOperator(suppliedToken);
      setToken("");
      dispatch({ type: "unlocked" });
    } catch (error) {
      setToken("");
      const message = error instanceof ApiError ? error.message : "Operator unlock failed.";
      dispatch({ type: "failed", message });
    }
  };

  if (state.phase === "checking") {
    return <GateMessage message="Checking operator session…" />;
  }
  if (state.phase === "unlocked") return children;

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <section className="w-full max-w-md rounded-2xl border border-zinc-700 bg-zinc-950/90 p-6 shadow-2xl">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-400">
          Restricted operations console
        </p>
        <h1 className="mt-3 text-2xl font-semibold text-zinc-100">Operator unlock required</h1>
        <p className="mt-2 text-sm leading-relaxed text-zinc-400">
          Enter the backend operator token to open a short-lived browser session. The token is
          exchanged for an HttpOnly cookie and is never saved by this frontend.
        </p>
        <form className="mt-6 space-y-4" onSubmit={submit}>
          <label className="block text-sm text-zinc-300">
            Operator token
            <input
              className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-zinc-100 outline-none focus:border-sky-500"
              type="password"
              value={token}
              minLength={32}
              maxLength={256}
              autoComplete="off"
              spellCheck={false}
              disabled={state.phase === "unlocking"}
              onChange={(event) => setToken(event.target.value)}
              aria-label="Operator token"
            />
          </label>
          {state.error && (
            <p role="alert" className="text-sm text-red-300">
              {state.error}
            </p>
          )}
          <button
            type="submit"
            disabled={state.phase === "unlocking"}
            className="w-full rounded-lg bg-sky-500 px-4 py-2 font-medium text-zinc-950 disabled:opacity-50"
          >
            {state.phase === "unlocking" ? "Unlocking…" : "Unlock console"}
          </button>
        </form>
      </section>
    </div>
  );
}

function GateMessage({ message }: Readonly<{ message: string }>) {
  return (
    <div className="flex min-h-screen items-center justify-center text-sm text-zinc-400">
      {message}
    </div>
  );
}

