import type { ButtonHTMLAttributes, ReactNode } from "react";
import { sourceLabel, truthLabel, validityLabel } from "../labels";

export type Tone =
  | "ok"
  | "warning"
  | "fault"
  | "critical"
  | "info"
  | "neutral"
  | "simulation"
  | "replay";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-header">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {subtitle && <p className="card-subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function StatusChip({ tone, label, detail }: { tone: Tone; label: string; detail?: string }) {
  return (
    <span className={`chip chip-${tone}`} title={detail}>
      <span className="chip-dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export function SourceBadge({ source }: { source: "LIVE" | "SIMULATION" | "REPLAY" }) {
  const tone: Tone = source === "SIMULATION" ? "simulation" : source === "REPLAY" ? "replay" : "ok";
  return (
    <span className={`source-badge source-${tone}`} title={`source=${source}`}>
      {sourceLabel(source)}
    </span>
  );
}

export function TruthBadge({ truth }: { truth: string }) {
  const tone: Tone =
    truth === "simulated"
      ? "simulation"
      : truth === "measured" || truth === "measured_on_demand"
        ? "ok"
        : truth === "commanded"
          ? "warning"
          : "neutral";
  return <StatusChip tone={tone} label={truthLabel(truth)} detail={`truth=${truth}`} />;
}

export function ValidityBadge({ validity }: { validity: string }) {
  const tone: Tone =
    validity === "valid" ? "ok" : validity === "stale" ? "warning" : validity === "invalid" ? "fault" : "neutral";
  return <StatusChip tone={tone} label={validityLabel(validity)} detail={`validity=${validity}`} />;
}

export function MetricValue({
  label,
  value,
  unit,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className={`metric metric-${tone}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">
        {value}
        {unit && <span className="metric-unit">{unit}</span>}
      </span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

export function DataAge({ ageMs, maxAgeMs }: { ageMs: number | null; maxAgeMs?: number }) {
  if (ageMs === null) {
    return <StatusChip tone="neutral" label="不可用" />;
  }
  const stale = maxAgeMs !== undefined && ageMs > maxAgeMs;
  return (
    <StatusChip
      tone={stale ? "fault" : "ok"}
      label={ageMs < 1000 ? `${Math.round(ageMs)} ms` : `${(ageMs / 1000).toFixed(1)} s`}
      detail={stale ? `超过 ${maxAgeMs} ms` : "新鲜"}
    />
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onDismiss && (
        <button type="button" className="link-button" onClick={onDismiss}>
          关闭
        </button>
      )}
    </div>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonProps) {
  return <button className={`button button-${variant} ${className}`} {...props} />;
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  if (!open) {
    return null;
  }
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <h2>{title}</h2>
          <button type="button" className="link-button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="key-value">
      <span className="key-value-label">{label}</span>
      <span className="key-value-value">{children}</span>
    </div>
  );
}
