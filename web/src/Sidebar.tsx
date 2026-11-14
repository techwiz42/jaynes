import { type ConversationSummary } from "./api";

export function relativeTime(seconds: number, now: number = Date.now() / 1000): string {
  const age = Math.max(0, now - seconds);
  if (age < 60) return "just now";
  if (age < 3600) return `${Math.floor(age / 60)} min ago`;
  if (age < 86400) return `${Math.floor(age / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

/** The user's saved conversations, newest first, with a button for a new one. */
export function Sidebar({
  conversations,
  current,
  onOpen,
  onNew,
  onDelete,
}: {
  conversations: ConversationSummary[];
  current: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <nav className="sidebar" aria-label="Conversations">
      <button type="button" className="new" onClick={onNew}>
        New conversation
      </button>
      {conversations.length === 0 ? (
        <p className="empty">Nothing saved yet.</p>
      ) : (
        <ul>
          {conversations.map((conversation) => (
            <li key={conversation.id} className={conversation.id === current ? "current" : ""}>
              <button type="button" className="open" onClick={() => onOpen(conversation.id)} aria-current={conversation.id === current ? "true" : undefined}>
                <span className="title">{conversation.title}</span>
                <span className="when">{relativeTime(conversation.updated_at)}</span>
              </button>
              <button type="button" className="delete" aria-label={`Delete ${conversation.title}`} title="Delete" onClick={() => onDelete(conversation.id)}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
