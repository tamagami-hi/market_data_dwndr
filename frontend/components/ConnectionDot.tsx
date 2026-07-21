"use client";

import type { TopicConnection } from "@/lib/wsTopicConnection";
import { useConnectionState } from "@/lib/useTopic";

export default function ConnectionDot({
  connection,
  label,
}: {
  connection: TopicConnection;
  label: string;
}) {
  const state = useConnectionState(connection);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
      <span
        className={`inline-block h-2 w-2 rounded-full ${
          state.connected ? "bg-green-500 shadow-[0_0_6px] shadow-green-500" : "bg-red-500"
        }`}
      />
      {label}
    </span>
  );
}
