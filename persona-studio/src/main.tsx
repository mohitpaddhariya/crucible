import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import App from "./App.tsx"
import "./index.css"

const container = document.getElementById("root")
if (container === null) {
  // Nothing can be rendered without it, and a blank page with no explanation is worse
  // than a loud failure at the one moment the cause is still obvious.
  throw new Error("Missing #root element in index.html")
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
