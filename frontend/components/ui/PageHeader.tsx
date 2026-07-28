import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="min-w-0">
        {eyebrow && <p className="label text-accent">{eyebrow}</p>}
        <h1 className="text-lg font-semibold tracking-tight text-primary sm:text-xl">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-secondary">{description}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  );
}
