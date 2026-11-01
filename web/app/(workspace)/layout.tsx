"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Rail } from "@/components/rail";
import { getSession, refreshSession, subscribe } from "@/lib/auth";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function check() {
      // A cached session paints at once; the server confirms it either way, because
      // the cookie may have expired since it was cached.
      if (getSession()) setReady(true);
      const live = await refreshSession();
      if (cancelled) return;
      if (!live) router.replace("/login");
      else setReady(true);
    }
    void check();
    const unsubscribe = subscribe(() => {
      if (!getSession()) router.replace("/login");
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [router]);

  // Render nothing until the session is confirmed. A flash of the authenticated
  // shell before redirecting looks broken and briefly implies access.
  if (!ready) return null;

  return (
    <div className="relative z-10 flex h-dvh overflow-hidden">
      <Rail />
      <main className="flex min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
