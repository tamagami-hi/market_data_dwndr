import type { ReactNode } from "react";

export function MarketPageHeader({
  title,
  description,
  children,
  ariaLabel,
}: {
  title: string;
  description: string;
  children: ReactNode;
  ariaLabel: string;
}) {
  return (
    <header className="market-page-header" aria-label={ariaLabel}>
      <h1 className="shrink-0 whitespace-nowrap text-lg font-semibold tracking-tight text-primary sm:text-xl">
        {title}
      </h1>
      <p className="sr-only">{description}</p>
      {children}
    </header>
  );
}
