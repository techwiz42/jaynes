import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createSession, currentUser, login, readAttachment } from "./api";

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("the API client", () => {
  it("says nobody is logged in on a 401 instead of failing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(401, { detail: "log in first" })));
    expect(await currentUser()).toBeNull();
  });

  it("lets other failures through", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(500, {})));
    await expect(currentUser()).rejects.toBeInstanceOf(ApiError);
  });

  it("carries the server's own explanation", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(409, { detail: "that email is already registered" })));
    await expect(login("a@example.com", "x")).rejects.toMatchObject({ status: 409, message: "that email is already registered" });
  });

  it("falls back to a plain sentence when the server gave none", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    await expect(login("a@example.com", "x")).rejects.toMatchObject({ status: 502, message: "could not log in (502)" });
  });

  it("starts a new conversation without a body and continues one by id", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => reply(200, { session_id: "s1", conversation_id: "c1", live: false }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await createSession()).toEqual({ sessionId: "s1", conversationId: "c1", live: false });
    expect(fetchMock.mock.calls[0]![1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[0]![1]?.body).toBeUndefined();
    await createSession("c9");
    expect(JSON.parse(fetchMock.mock.calls[1]![1]?.body as string)).toEqual({ conversation_id: "c9" });
  });

  it("sends a file's own bytes with its name in the query, never in a form", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => reply(200, { name: "trip & plans.md", text: "# Trip", chars: 6, truncated: false }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["# Trip"], "trip & plans.md");
    await readAttachment(file);
    expect(fetchMock.mock.calls[0]![0]).toBe("/attachments?name=trip%20%26%20plans.md");
    expect(fetchMock.mock.calls[0]![1]?.body).toBe(file);
  });
});
