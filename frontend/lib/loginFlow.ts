export type BackendLoginStep = "awaiting_totp" | "awaiting_risk_free_rate";
export type LoginMethod = "shared_session" | "local_credentials";
export type LoginFlowStep = "idle" | "starting" | "totp" | "rate" | "success";

export interface LoginFlowState {
  step: LoginFlowStep;
  attemptId: string | null;
  method: LoginMethod | null;
  error: string | null;
}

export type LoginFlowAction =
  | { type: "start" }
  | {
      type: "started";
      attemptId: string;
      backendStep?: BackendLoginStep;
      method?: LoginMethod;
    }
  | { type: "totpAccepted" }
  | { type: "succeeded" }
  | { type: "failed"; message: string }
  | { type: "failedAndReset"; message: string }
  | { type: "reset" };

export const initialLoginFlowState: LoginFlowState = {
  step: "idle",
  attemptId: null,
  method: null,
  error: null,
};

export function loginFlowReducer(
  state: LoginFlowState,
  action: LoginFlowAction,
): LoginFlowState {
  switch (action.type) {
    case "start":
      return { ...initialLoginFlowState, step: "starting" };
    case "started":
      return {
        step: action.backendStep === "awaiting_risk_free_rate" ? "rate" : "totp",
        attemptId: action.attemptId,
        method: action.method ?? null,
        error: null,
      };
    case "totpAccepted":
      return { ...state, step: "rate", error: null };
    case "succeeded":
      return { ...state, step: "success", error: null };
    case "failed":
      return { ...state, error: action.message };
    case "failedAndReset":
      return { ...initialLoginFlowState, error: action.message };
    case "reset":
      return { ...initialLoginFlowState };
  }
}

export function isValidTotp(value: string): boolean {
  return /^[0-9]{6}$/.test(value);
}

export function parseRiskFreeRate(value: string): number | null {
  if (!value.trim()) return null;
  const rate = Number(value);
  return Number.isFinite(rate) && rate >= 0 ? rate : null;
}
