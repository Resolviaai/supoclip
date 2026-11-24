import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function GET() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({
      enabled: false,
      intensities: [],
      defaults: {},
      settings: {},
      music_moods: [],
      grades: [],
      templates: [],
      reactions_available: false,
    });
  }

  try {
    const upstream = await fetchBackend("/tasks/edit-styles", {
      method: "GET",
      userId: session.user.id,
      cache: "no-store",
    });
    return createTextProxyResponse(upstream);
  } catch (err) {
    console.error("Failed to proxy /tasks/edit-styles:", err);
    return NextResponse.json({ error: "Failed to fetch edit styles" }, { status: 502 });
  }
}
