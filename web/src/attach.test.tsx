import { afterEach, describe, expect, it, vi } from "vitest";
import { readAttachment, sendMessage } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("sending files with a message", () => {
  it("posts the file's bytes under its name and returns the text the server read", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ name: "a b.xlsx", text: "x", chars: 1, truncated: false }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["data"], "a b.xlsx");
    expect((await readAttachment(file)).text).toBe("x");
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/attachments?name=a%20b.xlsx");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(file);
  });

  it("says why the server refused a file", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "that PDF is password-protected" }), { status: 422 })));
    await expect(readAttachment(new File(["x"], "s.pdf"))).rejects.toThrow("that PDF is password-protected");
  });

  it("sends only the names and text of the attachments with the message", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    await sendMessage("s1", "read this", [{ name: "plan.txt", text: "open at nine", extra: 1 } as never]);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ text: "read this", attachments: [{ name: "plan.txt", text: "open at nine" }] });
  });
});
