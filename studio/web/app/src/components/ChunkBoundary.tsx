// Keeps a tab's failure from blanking the whole page. A lazily loaded file that a newer deploy removed (see
// lib/chunkReload.ts) reloads the page once; anything else shows a short message with a Reload button.
import { Component, type ReactNode } from "react";
import { isChunkError, reloadOnce } from "../lib/chunkReload";

export class ChunkBoundary extends Component<{ children: ReactNode }, { failed: boolean; reloading: boolean }> {
  state = { failed: false, reloading: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    if (isChunkError(error) && reloadOnce()) this.setState({ reloading: true });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="empty" role="alert">
        <p>{this.state.reloading ? "Loading the latest version of this page…" : "This part of the page could not load."}</p>
        {this.state.reloading ? null : (
          <button className="btn sm" type="button" onClick={() => window.location.reload()}>
            Reload the page
          </button>
        )}
      </div>
    );
  }
}
