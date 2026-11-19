import IdeaForm from "@/components/IdeaForm";
import ui from "@/components/ui.module.css";

export default function Home() {
  return (
    <main className={ui.shell}>
      <span className={ui.brand}>NOVENTRA</span>
      <h1 className={ui.h1}>Test your business idea before you build it.</h1>
      <p className={ui.lead}>
        Describe the idea. NOVENTRA runs a pre-mortem, estimates the economics with a deterministic
        model, scores risk, and gives a Go, Go with changes, or Rethink verdict with reasons.
      </p>
      <IdeaForm />
    </main>
  );
}
