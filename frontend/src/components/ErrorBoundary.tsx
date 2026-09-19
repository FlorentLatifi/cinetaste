import { Component, type ErrorInfo, type ReactNode } from "react";
import { captureFrontendError } from "../observability";

type Props = {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
};

type State = {
  hasError: boolean;
  error: Error | null;
};

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    captureFrontendError(error, { componentStack: info.componentStack ?? "" });
    this.props.onError?.(error, info);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    if (this.props.fallback) return this.props.fallback;

    return (
      <div className="center-screen error-boundary">
        <div className="error-boundary-card">
          <span aria-hidden="true" className="error-boundary-icon">!</span>
          <h1 className="error-boundary-title">Something went wrong</h1>
          <p className="error-boundary-message">
            {this.state.error?.message || "An unexpected error occurred."}
          </p>
          <div className="error-boundary-actions">
            <button
              type="button"
              className="btn primary"
              onClick={this.handleReset}
            >
              Try again
            </button>
            <a href="/" className="btn ghost">
              Go home
            </a>
          </div>
        </div>
      </div>
    );
  }
}
