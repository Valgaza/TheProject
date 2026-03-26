import { useCallback } from "react";
import { ingestMessage } from "@/lib/memory-api";

const DEFAULT_USER_ID = "user_1";

interface UseMemoryIngestOptions {
  userId?: string;
  projectId?: string;
}

export function useMemoryIngest({ userId = DEFAULT_USER_ID, projectId }: UseMemoryIngestOptions) {
  const ingest = useCallback(
    (message: string) => {
      if (!message.trim() || !projectId) return;
      ingestMessage(message, userId, projectId);
    },
    [userId, projectId]
  );

  return ingest;
}
