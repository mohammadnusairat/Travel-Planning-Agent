import { describe, expect, it } from "vitest";
import { createSseParser } from "./useRunEvents";

describe("createSseParser", () => {
  it("parses events split across arbitrary chunks", () => {
    const messages: { event: string; data: string; id?: string }[] = [];
    const parse = createSseParser((m) => messages.push(m));
    parse('id: 7\nevent: step\ndata: {"a"');
    expect(messages).toEqual([]);
    parse(': 1}\n\n: keep-alive\n\nevent: end\ndata: {}\n\n');
    expect(messages).toEqual([
      { id: "7", event: "step", data: '{"a": 1}' },
      { event: "end", data: "{}" },
    ]);
  });

  it("handles CRLF line endings and multi-line data", () => {
    const messages: { event: string; data: string }[] = [];
    const parse = createSseParser((m) => messages.push(m));
    parse("data: line one\r\ndata: line two\r\n\r\n");
    expect(messages).toEqual([{ event: "message", data: "line one\nline two" }]);
  });
});
