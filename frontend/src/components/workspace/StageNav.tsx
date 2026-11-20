"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { stageStates } from "@/lib/stages";
import { useWorkspace } from "./WorkspaceProvider";
import styles from "./workspace.module.css";

export default function StageNav() {
  const { id, business } = useWorkspace();
  const pathname = usePathname();
  const stages = stageStates(business, id);

  return (
    <nav className={styles.nav} aria-label="Launch stages">
      <ol className={styles.navList}>
        {stages.map((s, i) => {
          const current = s.href !== null && pathname === s.href;
          const body = (
            <>
              <span className={styles.navIndex}>{i + 1}</span>
              <span className={styles.navText}>
                <span className={styles.navLabel}>{s.label}</span>
                {s.note && <span className={styles.navNote}>{s.note}</span>}
              </span>
            </>
          );
          return (
            <li key={s.key} className={styles.navItem}>
              {s.href ? (
                <Link
                  href={s.href}
                  className={`${styles.navLink} ${current ? styles.navCurrent : ""}`}
                  aria-current={current ? "page" : undefined}
                >
                  {body}
                </Link>
              ) : (
                <span className={`${styles.navLink} ${styles.navDisabled}`} aria-disabled="true">
                  {body}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
