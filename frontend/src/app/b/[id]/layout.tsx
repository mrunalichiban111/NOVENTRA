import type { ReactNode } from "react";
import WorkspaceProvider from "@/components/workspace/WorkspaceProvider";
import WorkspaceShell from "@/components/workspace/WorkspaceShell";

// Layouts are not re-rendered on navigation between /b/[id], /b/[id]/names and so on, so the
// provider (and its single polling loop) lives across the whole workspace.
export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <WorkspaceProvider id={id}>
      <WorkspaceShell>{children}</WorkspaceShell>
    </WorkspaceProvider>
  );
}
