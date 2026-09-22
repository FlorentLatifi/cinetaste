import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import App from "./App";
import { AuthProvider } from "./features/auth/AuthContext";
import { initColorScheme } from "./features/theme/colorScheme";
import { initFrontendObservability } from "./observability";
import "./styles/global.css";
import "./styles/layout.css";
import "./styles/auth.css";
import "./styles/landing.css";
import "./styles/for-you.css";
import "./styles/onboarding.css";
import "./styles/posters.css";
import "./styles/history.css";
import "./styles/title-detail.css";
import "./styles/account.css";
import "./styles/forced-colors.css";

initColorScheme();

void initFrontendObservability().finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <HelmetProvider>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </HelmetProvider>
    </StrictMode>,
  );
});
