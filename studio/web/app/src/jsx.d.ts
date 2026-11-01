// Custom elements used in JSX (esp-web-tools' install button for in-browser flashing).
import "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "esp-web-install-button": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & { manifest?: string };
    }
  }
}
