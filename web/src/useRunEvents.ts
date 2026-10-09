import { useEffect, useState } from "react";
import { API_BASE, type LiveState, type RunEvent } from "./api";

export interface RunStream {
  events: RunEvent[];
  state: LiveState | null;
  done: boolean;
  connected: boolean;
}

interface ParsedMessage {
  event: string;
  data: string;
  id?: string;
}

/** Incremental parser for the text/event-stream format. */
export function createSseParser(onMessage: (message: ParsedMessage) => void) {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk.replace(/\r\n/g, "\n");
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const message: ParsedMessage = { event: "message", data: "" };
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue; // comments are keep-alives
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") message.event = value;
        else if (field === "data") data.push(value);
        else if (field === "id") message.id = value;
      }
      if (data.length || message.event !== "message") {
        message.data = data.join("\n");
        onMessage(message);
      }
    }
  };
}

/**
 * Follow a run's server-sent event stream: replays history, then streams live.
 * Uses fetch rather than EventSource so it can resume with Last-Event-ID and back off between reconnects.
 */
export function useRunEvents(runId: string | undefined): RunStream {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [done, setDone] = useState(false);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    setDone(false);
    const controller = new AbortController();
    let lastId = 0;
    let finished = false;

    const connect = async () => {
      while (!finished && !controller.signal.aborted) {
        try {
          const response = await fetch(`${API_BASE}/api/runs/${runId}/events`, {
            headers: { Accept: "text/event-stream", ...(lastId ? { "Last-Event-ID": String(lastId) } : {}) },
            signal: controller.signal,
          });
          if (!response.ok || !response.body) {
            if (response.status === 404) return; // not retryable
            throw new Error(`stream failed: ${response.status}`);
          }
          setConnected(true);
          const parse = createSseParser((message) => {
            if (message.event === "end") {
              finished = true;
              setDone(true);
              return;
            }
            const event = JSON.parse(message.data) as RunEvent;
            if (event.id <= lastId) return; // replayed after a reconnect
            lastId = event.id;
            setEvents((previous) => [...previous, event]);
          });
          const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
          for (;;) {
            const { value, done: streamDone } = await reader.read();
            if (streamDone) break;
            parse(value);
          }
        } catch (error) {
          if (controller.signal.aborted) return;
        } finally {
          setConnected(false);
        }
        if (!finished) await new Promise((resolve) => setTimeout(resolve, 2000)); // reconnect after a drop
      }
    };
    connect();
    return () => controller.abort();
  }, [runId]);

  let state: LiveState | null = null;
  for (let i = events.length - 1; i >= 0 && !state; i--) {
    state = (events[i].data.state as LiveState | undefined) ?? null;
  }
  return { events, state, done, connected };
}
