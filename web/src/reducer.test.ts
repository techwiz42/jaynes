import { describe, expect, it } from "vitest";
import type { ChatEvent } from "./events";
import { describeTarget, initialState, isStreaming, reduce, reviewOf, type AssistantItem, type ChatState } from "./reducer";

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

describe("tool calls", () => {
  const call = (call_id: string, tool: string, args: Record<string, unknown>, turn_id = "t1"): ChatEvent => ({ id: id(), type: "tool_call", turn_id, call_id, tool, args });
  const result = (call_id: string, tool: string, ok: boolean, summary: string, truncated = false, turn_id = "t1"): ChatEvent => ({
    id: id(), type: "tool_result", turn_id, call_id, tool, ok, summary, truncated,
  });
  const tools = (state: ChatState) => state.items.filter((item) => item.kind === "tool");

  it("shows a call as running and then fills in its result", () => {
    let state = run([user("check the repo"), call("c1", "github_read", { operation: "read_file", repo: "acme/app", path: "src/a.py" })]);
    expect(tools(state)[0]).toMatchObject({ tool: "github_read", status: "running", target: "acme/app · src/a.py" });
    state = run([result("c1", "github_read", true, "read src/a.py (40 lines)")], state);
    expect(tools(state)[0]).toMatchObject({ status: "ok", summary: "read src/a.py (40 lines)", truncated: false });
  });

  it("marks a failed call and a truncated one", () => {
    const state = run([call("c1", "web_fetch", { url: "https://x.example/page" }), result("c1", "web_fetch", false, "refused: private address"), call("c2", "web_fetch", { url: "https://y.example" }), result("c2", "web_fetch", true, "read y.example", true)]);
    expect(tools(state).map((t) => [t.status, t.truncated])).toEqual([["failed", false], ["ok", true]]);
  });

  it("places the badges inline, between the question and the reply", () => {
    const state = run([user("q"), call("c1", "web_search", { query: "bicameral" }), result("c1", "web_search", true, "5 results"), token("m1", "Answer."), complete("m1", "Answer.")]);
    expect(state.items.map((i) => i.kind)).toEqual(["user", "tool", "assistant"]);
  });

  it("keeps calls with the same id in different turns apart", () => {
    const state = run([call("c1", "echo", {}, "t1"), result("c1", "echo", true, "one", false, "t1"), call("c1", "echo", {}, "t2"), result("c1", "echo", false, "two", false, "t2")]);
    expect(tools(state).map((t) => [t.turnId, t.status, t.summary])).toEqual([["t1", "ok", "one"], ["t2", "failed", "two"]]);
  });

  it("ignores a result whose call it has not seen, and a replayed event", () => {
    const stray = run([result("c9", "echo", true, "orphan")]);
    expect(tools(stray)).toHaveLength(0);
    const once = run([call("c1", "echo", {})]);
    expect(run([once.lastEventId ? ({ id: once.lastEventId, type: "tool_call", turn_id: "t1", call_id: "c1", tool: "echo", args: {} } as ChatEvent) : user("x")], once)).toBe(once);
  });

  it("describes a target briefly and never shows more than the first line of code", () => {
    expect(describeTarget({ code: "x = 1\nprint(secret)\n" })).toBe("x = 1");
    expect(describeTarget({ url: "https://example.com/" + "a".repeat(100) }).length).toBeLessThanOrEqual(71);
    expect(describeTarget({})).toBe("");
  });
});
