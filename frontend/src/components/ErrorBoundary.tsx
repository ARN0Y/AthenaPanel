import * as React from "react";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Surface for debugging in the console as well.
    // eslint-disable-next-line no-console
    console.error("UI crash:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-background p-6">
          <div className="w-full max-w-lg rounded-xl border border-destructive/40 bg-card p-6">
            <h1 className="mb-2 text-lg font-semibold text-destructive">Something went wrong</h1>
            <p className="mb-4 text-sm text-muted-foreground">
              The interface hit an unexpected error. Details below:
            </p>
            <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">
              {this.state.error.message}
              {"\n\n"}
              {this.state.error.stack}
            </pre>
            <button
              className="mt-4 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
              onClick={() => {
                this.setState({ error: null });
                window.location.reload();
              }}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
