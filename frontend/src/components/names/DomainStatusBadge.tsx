import type { DomainStatus } from "@/lib/names-api";
import styles from "./names.module.css";

// Three visually distinct states. UNKNOWN is its own state and is never styled or worded as
// available: it means the registrar could not be asked, not that the name is free.
const LABEL: Record<DomainStatus, string> = {
  AVAILABLE: "Likely available",
  UNAVAILABLE: "Taken",
  UNKNOWN: "Unknown",
};
const CLASS: Record<DomainStatus, string> = {
  AVAILABLE: styles.dAvailable,
  UNAVAILABLE: styles.dTaken,
  UNKNOWN: styles.dUnknown,
};

export default function DomainStatusBadge({ status }: { status: DomainStatus }) {
  return <span className={`${styles.domainBadge} ${CLASS[status]}`}>{LABEL[status]}</span>;
}
