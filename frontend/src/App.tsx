import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./features/auth/AuthContext";
import { AppShell } from "./components/AppShell";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { VerifyEmailPage } from "./pages/VerifyEmailPage";

/** Lazy-load heavier authenticated surfaces to shrink the initial guest bundle. */
const AccountPage = lazy(() =>
  import("./pages/AccountPage").then((m) => ({ default: m.AccountPage })),
);
const HistoryPage = lazy(() =>
  import("./pages/HistoryPage").then((m) => ({ default: m.HistoryPage })),
);
const OnboardingPage = lazy(() =>
  import("./pages/OnboardingPage").then((m) => ({ default: m.OnboardingPage })),
);
const SearchPage = lazy(() =>
  import("./pages/SearchPage").then((m) => ({ default: m.SearchPage })),
);
const TitleDetailPage = lazy(() =>
  import("./pages/TitleDetailPage").then((m) => ({ default: m.TitleDetailPage })),
);
const WatchlistPage = lazy(() =>
  import("./pages/WatchlistPage").then((m) => ({ default: m.WatchlistPage })),
);
const ForYouPage = lazy(() =>
  import("./pages/HomePage").then((m) => ({ default: m.HomePage })),
);

function RouteFallback() {
  return (
    <div className="center-screen">
      <div className="spinner" aria-label="Loading" />
    </div>
  );
}

function Protected({ children }: { children: ReactNode }) {
  const { user, loading, emailVerificationRequired } = useAuth();
  const { pathname } = useLocation();
  if (loading) {
    return (
      <div className="center-screen">
        <div className="spinner" aria-label="Loading" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  // The server is enforcing email verification. /account is the only page
  // that can resolve it, and none of its calls are gated, so no loop.
  if (emailVerificationRequired && pathname !== "/account") {
    return <Navigate to="/account" replace />;
  }
  return children;
}

function GuestOnly({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="center-screen">
        <div className="spinner" aria-label="Loading" />
      </div>
    );
  }
  if (user) return <Navigate to="/" replace />;
  return children;
}

/** Guests see marketing landing; signed-in users see immersive For You. */
function RootRoute() {
  const { user, loading, emailVerificationRequired } = useAuth();
  if (loading) {
    return (
      <div className="center-screen">
        <div className="spinner" aria-label="Loading" />
      </div>
    );
  }
  if (!user) return <LandingPage />;
  if (emailVerificationRequired) return <Navigate to="/account" replace />;
  return (
    <AppShell>
      <Suspense fallback={<RouteFallback />}>
        <ForYouPage />
      </Suspense>
    </AppShell>
  );
}

function LazyProtected({ children }: { children: ReactNode }) {
  return (
    <Protected>
      <AppShell>
        <Suspense fallback={<RouteFallback />}>{children}</Suspense>
      </AppShell>
    </Protected>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
    <Routes>
      <Route
        path="/login"
        element={
          <GuestOnly>
            <LoginPage />
          </GuestOnly>
        }
      />
      <Route
        path="/register"
        element={
          <GuestOnly>
            <RegisterPage />
          </GuestOnly>
        }
      />
      <Route
        path="/forgot-password"
        element={
          <GuestOnly>
            <ForgotPasswordPage />
          </GuestOnly>
        }
      />
      <Route
        path="/reset-password"
        element={
          <GuestOnly>
            <ResetPasswordPage />
          </GuestOnly>
        }
      />
      <Route
        path="/onboarding"
        element={
          <LazyProtected>
            <OnboardingPage />
          </LazyProtected>
        }
      />
      <Route
        path="/watchlist"
        element={
          <LazyProtected>
            <WatchlistPage />
          </LazyProtected>
        }
      />
      <Route
        path="/history"
        element={
          <LazyProtected>
            <HistoryPage />
          </LazyProtected>
        }
      />
      <Route
        path="/search"
        element={
          <LazyProtected>
            <SearchPage />
          </LazyProtected>
        }
      />
      <Route
        path="/account"
        element={
          <LazyProtected>
            <AccountPage />
          </LazyProtected>
        }
      />
      <Route
        path="/titles/:titleId"
        element={
          <LazyProtected>
            <TitleDetailPage />
          </LazyProtected>
        }
      />
      {/* Not guest-only and not protected: the link arrives by email and
          is often opened on a different device from the one used to sign
          up. The token is the proof. */}
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/" element={<RootRoute />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
    </ErrorBoundary>
  );
}
