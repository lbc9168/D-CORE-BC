import { Component, type ErrorInfo, type ReactNode } from "react";

interface ProvinceMapErrorBoundaryProps {
  children: ReactNode;
  countryIso3: string;
  countryName: string;
}

interface ProvinceMapErrorBoundaryState {
  hasError: boolean;
  message: string;
}

export default class ProvinceMapErrorBoundary extends Component<
  ProvinceMapErrorBoundaryProps,
  ProvinceMapErrorBoundaryState
> {
  state: ProvinceMapErrorBoundaryState = { hasError: false, message: "" };

  static getDerivedStateFromError(error: unknown): ProvinceMapErrorBoundaryState {
    const message =
      error instanceof Error ? error.message : typeof error === "string" ? error : "Unknown error";
    return { hasError: true, message };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("[ProvinceMapErrorBoundary] render failed:", error, info);
  }

  componentDidUpdate(prevProps: ProvinceMapErrorBoundaryProps) {
    if (prevProps.countryIso3 !== this.props.countryIso3 && this.state.hasError) {
      this.setState({ hasError: false, message: "" });
    }
  }

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-slate-400">
        <span className="text-2xl">🗺</span>
        <span className="text-sm">Province map render failed</span>
        {this.state.message && (
          <span className="text-xs text-red-400 max-w-[90%] text-center break-words">
            {this.state.message}
          </span>
        )}
        <span className="text-xs text-slate-300">
          {this.props.countryName} ({this.props.countryIso3})
        </span>
      </div>
    );
  }
}
