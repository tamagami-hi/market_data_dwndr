export type OperatorAuthPhase = "checking" | "locked" | "unlocking" | "unlocked";

export interface OperatorAuthState {
  phase: OperatorAuthPhase;
  error: string | null;
}

export type OperatorAuthAction =
  | { type: "checked"; isUnlocked: boolean }
  | { type: "unlocking" }
  | { type: "unlocked" }
  | { type: "locked" }
  | { type: "failed"; message: string };

export const initialOperatorAuthState: OperatorAuthState = {
  phase: "checking",
  error: null,
};

export function operatorAuthReducer(
  _state: OperatorAuthState,
  action: OperatorAuthAction,
): OperatorAuthState {
  if (action.type === "checked") {
    return { phase: action.isUnlocked ? "unlocked" : "locked", error: null };
  }
  if (action.type === "unlocking") return { phase: "unlocking", error: null };
  if (action.type === "unlocked") return { phase: "unlocked", error: null };
  if (action.type === "locked") return { phase: "locked", error: null };
  return { phase: "locked", error: action.message };
}

export function isValidOperatorToken(value: string): boolean {
  const tokenLength = value.trim().length;
  return tokenLength >= 32 && tokenLength <= 256;
}

