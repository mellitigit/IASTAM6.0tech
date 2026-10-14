import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import CursorHalo from "./cursor-halo";
import RouteTransition from "./route-transition";

export const metadata: Metadata = {
  title: "Astra Mission Intelligence — Every byte has a mission",
  description:
    "A safety-first satellite brain that decides what to process, protect, compress and send, with clear ground-control tools for every mission.",
};

// Dev only: browser extensions (e.g. Bitdefender's `bis_skin_checked`) inject
// attributes into the DOM before hydration, which floods the dev overlay with
// hydration-mismatch warnings. Strip them as they appear.
const stripExtensionAttributes = `(() => {
  const attrs = ["bis_skin_checked", "bis_register", "cz-shortcut-listen"];
  const clean = (el) => {
    for (const a of attrs) el.removeAttribute(a);
    for (const a of [...el.attributes]) if (a.name.startsWith("__processed_")) el.removeAttribute(a.name);
  };
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "attributes") clean(r.target);
      else for (const n of r.addedNodes) if (n.nodeType === 1) {
        clean(n);
        n.querySelectorAll("*").forEach(clean);
      }
    }
  }).observe(document.documentElement, { subtree: true, childList: true, attributeFilter: attrs });
  document.querySelectorAll("*").forEach(clean);
})();`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased" data-scroll-behavior="smooth" suppressHydrationWarning>
      <body className="min-h-full flex flex-col" suppressHydrationWarning>
        {process.env.NODE_ENV === "development" && (
          <Script id="strip-extension-attrs" strategy="beforeInteractive">
            {stripExtensionAttributes}
          </Script>
        )}
        <CursorHalo />
        <RouteTransition>{children}</RouteTransition>
      </body>
    </html>
  );
}
