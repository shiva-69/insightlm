"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Rail } from "@/components/rail";
import { getToken, subscribe } from "@/lib/auth";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    function check() {
      if (!getToken()) {
        router.replace("/login");
        return;
      }
      setReady(true);
    }
    check();
    return subscribe(check);
  }, [router]);

  // Render nothing until the token is confirmed. A flash of the authenticated
  // shell before redirecting looks broken and briefly implies access.
  if (!ready) return null;

  return (
    <div className="relative z-10 flex h-dvh overflow-hidden">
      <Rail />
      <main className="flex min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
