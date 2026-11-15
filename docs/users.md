# Accounts

A user (registration, login) with saved conversations of their own. Decisions made with the owner: registration is **open**; the tools that use one shared set of credentials (Gmail, Drive, GitHub, `DOCS_DIR`) are **off** until each user can connect their own.

## What a user gets

- Register with an email and a password (at least 10 characters), log in, log out. The login is an HttpOnly, SameSite=Strict cookie valid for 30 days.
- A sidebar of their saved conversations. A conversation is saved with its first message (opening the page leaves nothing behind) and can be continued, including after a restart: its earlier messages go back to the model.
- Files sent with a message (PDF, Word, Excel, text): the text goes to the model for that message; nothing is stored apart from the file's name in the message.
- An Account panel: delete the whole account with everything in it (password required).

## What is stored, and where

One SQLite file, `jaynes.db` in `DATA_DIR` (`~/jaynes/data` on melville), created with mode 600. Tables: users, login sessions, conversations and messages.

Passwords are stored as salted scrypt hashes (N=2^15, r=8, p=1), never in the clear and never logged. A login token is random, sent to the browser once, and stored only as its SHA-256, so a copy of the database cannot be used to log in. Logging out, or deleting the account, ends the login on the server.

## Keeping users apart

- Every store call that touches a user's data takes the user's id and filters on it **in the SQL**: conversation reads and writes and deletes. There is no call that returns another user's rows, and the tests check each route and each store function with two users.
- A session belongs to one user. Another user's session id or conversation id gets `404`, the same as one that does not exist.
- A conversation id that exists under another user cannot be written into, even by asking for it to be created.

## Abuse and limits

- Failed logins are throttled: 5 per email and 30 per address in 15 minutes, then `429` with `Retry-After`. Registration is limited to 20 per address per hour. Behind a proxy (`BEHIND_PROXY=1`) the address is the visitor's, taken from the proxy; through an SSH tunnel every request comes from localhost, so the per-address limits are weak and the per-email limit is the one that matters. A wrong email and a wrong password give the same answer and take about the same time.
- Cookies do not tell ports apart, so a page on another localhost port could otherwise act as a logged-in user. Every state-changing request that carries an `Origin` must carry this server's own address, or it is refused.
- Files sent with a message: at most `MAX_UPLOAD_BYTES` (10 MB) each and five per message; PDFs (at most 300 pages, no passwords, scans without text refused), Word and Excel files (unpacked size capped) and text. At most 100,000 characters of a file, and 250,000 of a message's files, go to the model. Attaching a file counts as reading private data: web search and fetch are refused for the rest of that conversation.
- Spending is capped (see [`public.md`](public.md)): messages per hour and estimated tokens per day for each user, estimated tokens per day for everybody, and new accounts per day. When the day's total is reached Jaynes rests until midnight UTC. Set budget alerts with the providers as well.
- Not built: email verification, password reset, two-factor login, an admin page. A forgotten password cannot be recovered; the owner can delete the user from the database.

## Shared tools are off

`gmail_read`, `drive_read`, `github_read` and `retrieve_docs` use credentials that belong to the owner. With accounts, every user would reach them, so they are off unless `SHARED_PRIVATE_TOOLS=1`. Per-user connections (each user signs in to their own Google account) are future work.

## Deploying

`scripts/update-jaynes.sh` installs the dependencies and restarts the service; `deploy/run.sh` sets `DATA_DIR` to `~/jaynes/data`, outside the code checkout, so a deploy never touches the database. The restart ends open conversations, but saved ones come back. Back up `~/jaynes/data/jaynes.db`: it is the only copy of every user's data. The first deploy of this change creates the file and, as before, needs `ALLOW_PRIVATE_EGRESS=1` before the memory tools appear.
