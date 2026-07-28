import type { ReactNode } from "react";

export function PageFrame({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`page-frame ${className}`}>{children}</div>;
}
