import IdeaForm from "@/components/IdeaForm";
import Link from "next/link";
import ui from "@/components/ui.module.css";

export default function BuildPage() {
  return (
    <main className={ui.shell}>
      <Link href="/" className={ui.brand}>NOVENTRA</Link>
      <h1 className={ui.h1}>Test your business idea before you build it.</h1>
      <p className={ui.lead}>
        Describe the idea. NOVENTRA runs a pre-mortem, estimates the economics with a deterministic
        model, scores risk, and gives a Go, Go with changes, or Rethink verdict with reasons.
      </p>
      <IdeaForm />
    </main>
  );
}
