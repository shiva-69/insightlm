"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { refreshSession } from "@/lib/auth";

export default function Index() {
  const router = useRouter();
  useEffect(() => {
    void refreshSession().then((session) => router.replace(session ? "/ask" : "/login"));
  }, [router]);
  return null;
}
