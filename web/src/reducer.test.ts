import { describe, expect, it } from "vitest";
import type { ChatEvent } from "./events";
import { initialState, isStreaming, reduce, reviewOf, type AssistantItem, type ChatState } from "./reducer";

let counter = 0;
const id = (): number => ++counter;
const run = (events: ChatEvent[], from: ChatState = initialState): ChatState => events.reduce(reduce, from);
const assistants = (state: ChatState): AssistantItem[] => state.items.filter((item): item is AssistantItem => item.kind === "assistant");

const user = (text: string): ChatEvent => ({ id: id(), type: "user_message", text });
const token = (message_id: string, text: string, kind: "draft" | "correction" = "draft", corrects?: string): ChatEvent => ({
  id: id(), type: "token", message_id, kind, text, ...(corrects ? { corrects } : {}),
});
const complete = (message_id: string, text: string): ChatEvent => ({ id: id(), type: "draft_complete", message_id, text });
const verdict = (message_id: string, retract: boolean): ChatEvent => ({
  id: id(), type: "observer_verdict", message_id, verdict: retract ? "retract" : "ok", severity: retract ? "high" : "none",
  issues: retract ? ["wrong date"] : [], correction_guidance: retract ? "give the right date" : "",
});
const retraction = (message_id: string, text: string): ChatEvent => ({ id: id(), type: "retraction", message_id, issues: ["wrong date"], text });

describe("token accumulation", () => {
  it("builds the reply from its tokens, as one message", () => {
    const state = run([user("hi"), token("m1", "Hel"), token("m1", "lo"), token("m1", "!")]);
    expect(assistants(state)).toHaveLength(1);
    expect(assistants(state)[0]).toMatchObject({ text: "Hello!", complete: false, role: "draft" });
    expect(isStreaming(state)).toBe(true);
  });

  it("takes the complete text as authoritative and stops streaming", () => {
    const state = run([token("m1", "Hel"), complete("m1", "Hello there")]);
    expect(assistants(state)[0]).toMatchObject({ text: "Hello there", complete: true });
    expect(isStreaming(state)).toBe(false);
  });

  it("keeps the replies of different messages apart", () => {
    const state = run([token("m1", "one"), complete("m1", "one"), token("m2", "two")]);
    expect(assistants(state).map((a) => a.text)).toEqual(["one", "two"]);
  });
});

describe("observer verdicts", () => {
  it("records a verdict next to its message, and an ok verdict leaves the message alone", () => {
    const state = run([token("m1", "fine"), complete("m1", "fine"), verdict("m1", false)]);
    expect(reviewOf(state, "m1")).toMatchObject({ verdict: "ok" });
    expect(assistants(state)[0]).toMatchObject({ contested: false });
  });
});

describe("retraction", () => {
  const events = (): ChatEvent[] => [
    user("when?"),
    token("m1", "In 1999."), complete("m1", "In 1999."),
    verdict("m1", true),
    retraction("m1", "In 1999."),
    token("m2", "In 2001.", "correction", "m1"), complete("m2", "In 2001."),
  ];

  it("marks the draft contested, keeps its text, and lists the issues", () => {
    const [draft] = assistants(run(events()));
    expect(draft).toMatchObject({ messageId: "m1", contested: true, text: "In 1999.", retractionIssues: ["wrong date"] });
  });

  it("places the correction after the draft as its own message", () => {
    const state = run(events());
    expect(state.items.map((item) => (item.kind === "assistant" ? `${item.role}:${item.messageId}` : item.kind))).toEqual([
      "user", "draft:m1", "correction:m2",
    ]);
    expect(assistants(state)[1]).toMatchObject({ role: "correction", corrects: "m1", contested: false, text: "In 2001." });
  });

  it("keeps the draft and the correction streams separate while the correction streams", () => {
    const state = run(events().slice(0, 5).concat([token("m2", "In ", "correction", "m1"), token("m2", "2001", "correction", "m1")]));
    const [draft, correction] = assistants(state);
    expect(draft?.text).toBe("In 1999.");
    expect(correction?.text).toBe("In 2001");
    expect(isStreaming(state)).toBe(true);
  });

  it("shows the review of the draft with its severity and guidance", () => {
    expect(reviewOf(run(events()), "m1")).toMatchObject({ verdict: "retract", severity: "high", issues: ["wrong date"], correctionGuidance: "give the right date" });
  });
});

describe("replays after a reconnect", () => {
  it("ignores events it has already applied", () => {
    const first: ChatEvent[] = [user("hi"), token("m1", "a"), token("m1", "b")];
    const once = run(first);
    const again = run(first, once);
    expect(again).toBe(once);
    expect(assistants(again)[0]?.text).toBe("ab");
  });

  it("carries on from the last event after a partial replay", () => {
    const events: ChatEvent[] = [user("hi"), token("m1", "a"), token("m1", "b"), token("m1", "c")];
    const state = run([...events.slice(0, 3), ...events.slice(1)]);        // the second connection replays two events it had
    expect(assistants(state)[0]?.text).toBe("abc");
    expect(state.lastEventId).toBe(events[3]?.id);
  });
});

describe("errors", () => {
  it("shows an error inline, with where it came from", () => {
    const state = run([user("hi"), { id: id(), type: "error", where: "observer", message: "the observer did not answer in time" }]);
    expect(state.items.at(-1)).toMatchObject({ kind: "error", where: "observer", message: "the observer did not answer in time" });
  });

  it("an observer error leaves the finished draft as it was", () => {
    const state = run([token("m1", "ok"), complete("m1", "ok"), { id: id(), type: "error", where: "observer", message: "x" }]);
    expect(assistants(state)[0]).toMatchObject({ complete: true, interrupted: false, contested: false });
  });

  it("an actor error marks the reply it cut short as interrupted", () => {
    const state = run([user("hi"), token("m1", "par"), { id: id(), type: "error", where: "actor", message: "x" }]);
    expect(assistants(state)[0]).toMatchObject({ interrupted: true, complete: false });
    expect(isStreaming(state)).toBe(false);
  });

  it("a failed correction is shown after the contested draft", () => {
    const state = run([
      token("m1", "bad"), complete("m1", "bad"), verdict("m1", true), retraction("m1", "bad"),
      { id: id(), type: "error", where: "actor_correction", message: "x" },
    ]);
    expect(state.items.map((item) => item.kind)).toEqual(["assistant", "error"]);
    expect(assistants(state)[0]).toMatchObject({ contested: true });
  });
});
