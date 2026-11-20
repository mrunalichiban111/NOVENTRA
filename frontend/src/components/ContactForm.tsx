"use client";

import { FormEvent, useState } from "react";
import { ApiError, submitContact } from "../lib/api";
import styles from "./ContactForm.module.css";

export default function ContactForm() {
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [error, setError] = useState("");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("loading");
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await submitContact({
        name: String(form.get("name") ?? ""),
        email: String(form.get("email") ?? ""),
        message: String(form.get("message") ?? ""),
        website: String(form.get("website") ?? ""),
      });
      setError("");
      event.currentTarget.reset();
      setState("success");
    } catch (caught) {
      const message = caught instanceof ApiError
        ? caught.message
        : "Could not send your message. Please try again.";
      setError(message);
      setState("error");
    }
  }

  return (
    <form className={styles.form} onSubmit={onSubmit} noValidate>
      <div className={styles.fields}>
        <label>
          Name
          <input name="name" required minLength={1} maxLength={100} autoComplete="name" />
        </label>
        <label>
          Email
          <input name="email" type="email" required autoComplete="email" />
        </label>
      </div>
      <label>
        Message
        <textarea name="message" required minLength={10} maxLength={2000} rows={5} />
      </label>
      <label className={styles.honeypot} aria-hidden="true">
        Website
        <input name="website" tabIndex={-1} autoComplete="off" />
      </label>
      <button type="submit" disabled={state === "loading"}>
        {state === "loading" ? "Sending..." : "Send message"} <span aria-hidden="true">↗</span>
      </button>
      <div className={styles.feedback} role={state === "error" ? "alert" : "status"} aria-live="polite">
        {state === "success" && "Thanks — your message has been sent."}
        {state === "error" && error}
      </div>
    </form>
  );
}
