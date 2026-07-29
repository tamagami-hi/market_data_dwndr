import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div className="panel-title-line">
          <h2 className="shrink-0 text-xs font-semibold text-primary">{title}</h2>
          {subtitle && <span className="truncate text-xs text-muted">{subtitle}</span>}
        </div>
        {action}
      </div>
      <div className="panel-body">{children}</div>
    </section>
  );
}
