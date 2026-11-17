import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AuthForm } from "./Auth";
import { relativeTime, Sidebar } from "./Sidebar";

describe("the login form", () => {
  it("starts on logging in, with a way to register", () => {
    const html = renderToStaticMarkup(<AuthForm onAuthenticated={() => undefined} />);
    expect(html).toContain('aria-label="Log in"');
    expect(html).toContain("current-password");
    expect(html).toContain("Create an account");
    expect(html).not.toContain("At least 10 characters");
  });
});

describe("the conversation list", () => {
  const conversations = [
    { id: "a", title: "Trip to Oslo", updated_at: 1_000 },
    { id: "b", title: "<script>alert(1)</script>", updated_at: 500 },
  ];
  const render = (current: string | null) =>
    renderToStaticMarkup(<Sidebar conversations={conversations} current={current} onOpen={() => undefined} onNew={() => undefined} onDelete={() => undefined} />);

  it("lists them and marks the open one", () => {
    const html = render("a");
    expect(html).toContain("Trip to Oslo");
    expect(html).toContain('aria-current="true"');
    expect(html).toContain("New conversation");
  });

  it("shows a title as text, never as markup", () => {
    const html = render(null);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("says when there is nothing saved", () => {
    const html = renderToStaticMarkup(<Sidebar conversations={[]} current={null} onOpen={() => undefined} onNew={() => undefined} onDelete={() => undefined} />);
    expect(html).toContain("Nothing saved yet.");
  });
});

describe("relativeTime", () => {
  it("speaks in minutes and hours, then dates", () => {
    expect(relativeTime(1000, 1010)).toBe("just now");
    expect(relativeTime(1000, 1000 + 5 * 60)).toBe("5 min ago");
    expect(relativeTime(1000, 1000 + 3 * 3600)).toBe("3 h ago");
    expect(relativeTime(1000, 1000 + 3 * 86400)).toMatch(/\d/);
  });
});

describe("the footer", () => {
  it("names the author, gives the email, the year and links to the repository", async () => {
    const { Footer, REPOSITORY } = await import("./Footer");
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain("© 2026 Peter Sisk");
    expect(html).toContain('href="mailto:pete@cyberiad.ai"');
    expect(html).toContain(`href="${REPOSITORY}"`);
    expect(REPOSITORY).toBe("https://github.com/techwiz42/jaynes");
    expect(html).toContain('rel="noopener noreferrer"');
  });
});

describe("the usage page", () => {
  it("lists each account with its tokens, logins and last login, and says 'never' for no login", async () => {
    const { UsageTable } = await import("./Usage");
    const html = renderToStaticMarkup(
      <UsageTable
        rows={[
          { email: "heavy@example.com", tokens: 1234567, login_count: 12, last_login_at: 1_700_000_000 },
          { email: "idle@example.com", tokens: 0, login_count: 0, last_login_at: null },
        ]}
      />,
    );
    expect(html).toContain("heavy@example.com");
    expect(html).toContain((1234567).toLocaleString());
    expect(html).toContain(">12<");
    expect(html).toContain("never");
    expect(html).toContain("Last login");
  });

  it("escapes what an account is called", async () => {
    const { UsageTable } = await import("./Usage");
    const html = renderToStaticMarkup(<UsageTable rows={[{ email: "<b>x</b>@example.com", tokens: 1, login_count: 1, last_login_at: 1 }]} />);
    expect(html).not.toContain("<b>x</b>");
  });
});
