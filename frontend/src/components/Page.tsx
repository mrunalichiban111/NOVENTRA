import type { ReactNode } from "react";
import ui from "./ui.module.css";

// Shared presentation primitives used by every product stage. Purely visual: no data, no state.

export function PageHeader({ eyebrow, title, lead }: { eyebrow?: string; title: string; lead?: ReactNode }) {
  return (
    <header className={ui.pageHead}>
      {eyebrow && <p className={ui.eyebrow}>{eyebrow}</p>}
      <h1 className={ui.pageTitle}>{title}</h1>
      {lead && <p className={ui.pageLead}>{lead}</p>}
    </header>
  );
}

export function SectionHead({ id, step, title, help }: { id: string; step?: number; title: string; help?: ReactNode }) {
  return (
    <>
      <div className={ui.sectionHead}>
        {step !== undefined && <span className={ui.sectionNum} aria-hidden="true">{step}</span>}
        <h2 id={id} className={ui.h2} style={{ margin: 0 }}>{title}</h2>
      </div>
      {help && <p className={ui.sectionHelp}>{help}</p>}
    </>
  );
}

type Tone = "info" | "success" | "warn" | "error";
const TONE: Record<Tone, string> = {
  info: ui.noticeInfo, success: ui.noticeSuccess, warn: ui.noticeWarn, error: ui.noticeError,
};

export function Notice({ tone = "info", title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <div className={`${ui.notice} ${TONE[tone]}`} role={tone === "error" ? "alert" : "status"}>
      {title && <p className={ui.noticeTitle}>{title}</p>}
      <div>{children}</div>
    </div>
  );
}

export function Loading({ children }: { children: ReactNode }) {
  return (
    <p className={ui.loading} role="status">
      <span className={ui.spin} aria-hidden="true" />
      <span>{children}</span>
    </p>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={ui.empty}>
      <p className={ui.emptyTitle}>{title}</p>
      {children}
    </div>
  );
}
