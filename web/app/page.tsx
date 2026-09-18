"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/auth";

export default function Index() {
  const router = useRouter();
  useEffect(() => {
    router.replace(getToken() ? "/ask" : "/login");
  }, [router]);
  return null;
}
