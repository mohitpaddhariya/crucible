import type { Metadata } from "next";
import App from "../App";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Dashboard | Crucible",
  description: "Inspect voice-agent evaluation runs, conversations, evidence, and defects.",
};

export default function DashboardPage() {
  return <App />;
}
