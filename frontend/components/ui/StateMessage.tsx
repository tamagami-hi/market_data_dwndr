import type { ReactNode } from "react";

import type { Severity } from "@/lib/monitor/severity";

export function StateMessage({
  title,
  children,
  severity = "neutral",
  role = "status",
  className = "",
}: {
  title: string;
  children?: ReactNode;
  severity?: Severity;
  role?: "status" | "alert";
  className?: string;
}) {
  return (
    <div className={`state-message state-${severity} ${className}`} role={role}>
      <p className="text-sm font-medium text-primary">{title}</p>
      {children && <div className="mt-1 text-xs leading-relaxed text-secondary">{children}</div>}
    </div>
  );
}
