import { describe, expect, it } from "vitest";
import { parseEvent } from "./events";

describe("parseEvent", () => {
  it("reads a known event", () => {
    expect(parseEvent('{"id": 3, "type": "token", "message_id": "m1", "kind": "draft", "text": "hi"}')).toMatchObject({ id: 3, type: "token", text: "hi" });
  });

  it.each(["", "not json", "null", "[]", '{"type": "token"}', '{"id": 1, "type": "mystery"}', '{"id": "1", "type": "token"}'])("rejects %j", (raw) => {
    expect(parseEvent(raw)).toBeNull();
  });
});
