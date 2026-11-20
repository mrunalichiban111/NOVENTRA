"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { Loading, Notice } from "../Page";
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
        {!business && !error && <Loading>Loading your workspace…</Loading>}
        {error && <Notice tone="error" title="We couldn’t load this business">{error}</Notice>}
        {business && children}
      </main>
    </div>
  );
}
