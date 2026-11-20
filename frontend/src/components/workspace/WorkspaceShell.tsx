"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import ui from "../ui.module.css";
import StageNav from "./StageNav";
import { useWorkspace } from "./WorkspaceProvider";
import styles from "./workspace.module.css";

export default function WorkspaceShell({ children }: { children: ReactNode }) {
  const { business, error } = useWorkspace();

  return (
    <div className={styles.frame}>
      <header className={styles.header}>
        <Link href="/build" className={ui.brand}>NOVENTRA</Link>
      </header>
      <StageNav />
      <main className={styles.content}>
        {!business && !error && <p className={ui.muted}>Loading…</p>}
        {error && <p className={ui.error} role="alert">{error}</p>}
        {business && children}
      </main>
    </div>
  );
}
