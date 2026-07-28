import type { Metadata } from "next";
import Landing from "./landing/Landing";
import "./landing/landing.css";

/**
 * `/` is the landing page; the dashboard lives at `/dashboard`.
 *
 * Both used to be the same component on two servers — Next on :3000 serving the dashboard
 * at `/`, and a second Vite server on :4173 serving the landing and proxying everything
 * back. A visitor had to know which port was which. One origin now serves both, so the
 * landing's own "Go to dashboard" link just works.
 *
 * The .landing-root wrapper is load-bearing: landing.css is rebased under it precisely so
 * the landing's *, html and body rules cannot reach the dashboard's Tailwind base.
 */
export const metadata: Metadata = {
  title: "Crucible — break your voice agent before your users do",
  description:
    "Synthetic Indian customers, built on Sarvam, call a live voice agent and report where it breaks.",
};

export default function Page() {
  return (
    <div className="landing-root">
      <Landing />
    </div>
  );
}
