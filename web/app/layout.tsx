import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Inter, JetBrains_Mono, Newsreader } from "next/font/google";
import { Providers } from "@/components/providers";
import "./globals.css";

/* Three families, three jobs. Chrome is scanned, prose is read, data is parsed —
   giving each its own face does more for legibility than any amount of spacing. */
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  axes: ["opsz"],
});
const newsreader = Newsreader({
  subsets: ["latin"],
  variable: "--font-newsreader",
  style: ["normal", "italic"],
  axes: ["opsz"],
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono-face",
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "InsightLM",
  description:
    "Ask questions of your documents and get answers where every claim cites the exact passage it came from.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfaf9" },
    { media: "(prefers-color-scheme: dark)", color: "#0e0d0c" },
  ],
};

/* Runs before paint so a dark-mode user never sees a white flash. */
const themeBootstrap = `(function(){try{var t=localStorage.getItem("insightlm.theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}})()`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // The per-request nonce from proxy.ts. Reading it also makes every page render per
  // request, which a nonce requires: a prerendered page would carry a stale one.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    /* The font variables go on <html>, not <body>.
       `--font-sans` is declared on :root and its value is `var(--font-inter), …`.
       A custom property is substituted in the scope where it is *declared*, so if
       `--font-inter` only exists on <body> the :root declaration resolves against
       nothing, becomes invalid, and every font silently falls back to the system
       stack — with the CSS still looking entirely correct. */
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${newsreader.variable} ${mono.variable}`}
    >
      <head>
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body className="grain antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
