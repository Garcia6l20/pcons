# The worker protocol

Some actions cost far more to start than to run: importing a large library,
opening a connection, or claiming a licence. A worker pays that cost once: it's a
long-lived server that runs those actions on request, over a local Unix socket.

The protocol consists of three processes:

- the **action** — the command the build script asked for; runs once per build edge
- the **client** — what the build tool runs in the action's place; runs once per build edge to invoke the action
- the **worker** — the server the client hands the action to; serves many clients

Pcons implements the client. This document is the contract with the worker:
what one must do, and what pcons guarantees in return. Anything that can listen
on a Unix socket can be one — a Python process, a compiled binary, a thin client
for a service already running — `pcons/workers/python_server.py` implements
a sample worker. For what workers are *for* and how a build script declares one, see
[Persistent Workers](user-guide.md#persistent-workers) in the user guide.

## How a worker is reached

`worker=` says how to start the worker. It has nothing to do with the action,
which `command=` gives as usual:

```python
env.Command(
    target="report.pdf",
    source="report.py",
    command="python $SOURCE --out $TARGET",
    worker=Worker(command=["my-worker", "--profile=render"]),
)
```

Pcons prepends the client to the action command in the generated `build.ninja` or the `Makefile`. At build time,
the client:

1. Connects to the worker's socket, if a worker is already listening there.
2. Otherwise starts one, by running the worker's start command with the socket
   path appended — `my-worker --profile=render /path/to/socket` — detached,
   with `PCONS_WORKER_IDLE_TIMEOUT` in its environment; then waits for the
   socket to appear.
3. Sends the action to the worker as one request, and exits with the status
   that comes back.
4. **Runs the action itself** if any of that fails.

Because of step 4, a worker is an optimization. A build that
cannot reach its worker is just slower, which is also what lets a generated
`build.ninja` work under plain ninja and in CI.

Two actions share a worker when their start command and (optional) `key` match.

## Reading the generated command

A worker turns a short command in the build script into a long one in
`build.ninja`. It is worth being able to read it, because this is what you see
when a build using a worker misbehaves:

```
command = <python> .../workers/client.py <socket> 30 4 <python> .../workers/python_server.py --preload xml.dom.minidom -- <python> $source_0 $source_1 $out
          └─────── the client ──────────┘ └──1──┘ └2┘ └3┘ └──────── how to start a worker (4 tokens) ────────────┘ └4┘ └──────── the action ────────┘
```

1. **The socket** this worker listens on, named after the `Worker`'s identity.
2. **The idle timeout**, passed to the worker in the environment when the
   client starts one.
3. **How many tokens the start command occupies.** A count rather than a
   separator, so a start command containing `--` cannot be misread as the end
   of one.
4. **`--`**, after which everything is the action itself — exactly the command
   the build script asked for, and this is what runs if no worker can be
   reached.

Two things follow that might be useful when something is wrong:

- **To run the action by hand, take everything after the `--`.** That is the
  command with the worker removed. Ask ninja for the version with its
  variables already filled in, and run it from the build directory:

  ```bash
  ninja -C build -t commands report.txt
  ```
- **To watch a worker start, run the start command yourself** with the socket
  path appended, and without redirecting its output.

The paths are absolute, including the interpreter's — a build
directory should keep working from any directory, and pin the tools it
was configured with rather than whichever happen to be on `PATH`
later.

## What a worker must do

**Listen on the socket path it was given.** `AF_UNIX`, `SOCK_STREAM`.
Bind a temporary name and `rename()` it into place. Create it mode
`0600` for security.

**Accept a request.** A message, UTF-8 JSON, carrying three file descriptors
as `SCM_RIGHTS` ancillary data — the client's stdin, stdout and stderr, in that
order. The JSON should have this form:

```json
{
  "argv": ["python", "render.py", "--out", "report.pdf"],
  "cwd": "/path/the/action/runs/in",
  "env": {"PATH": "...", "...": "..."},
  "stamp": "an opaque string identifying the client's environment"
}
```

**Run the action as the client would have.** In `cwd`, with `env` —
not with the environment the worker itself started in. Write its
output to the descriptors that came with the request: they are the build
tool's pipes, so nothing needs relaying, and stdout and stderr keep the
order the program produced them in.

**Serve every action in isolation.** For correctness and security, an
action must not be able to observe anything a previous action did.
`python_server.py` gets this by forking a child per request, but a
worker may guarantee this by other means.

**Reply with one JSON line**, and nothing else, on the socket connection:

```json
{"exit": 0}
```

`{"error": "..."}` instead, or a closed connection, means the action
did not run — the client will then run it directly. Refusing is always
safe. Put a human-readable reason in `error`: it gets shown when
`PCONS_WORKER_DEBUG=1`.

Other notes for workers:
- Adopt the first `stamp` received; refuse and exit when a later request carries a different one
- Refuse if isolation cannot be guaranteed
- Exit when idle, after `PCONS_WORKER_IDLE_TIMEOUT` seconds of inactivity

## When a worker is not being used

A refusal and an absent worker look identical from the outside: the build is
simply slower. Set `PCONS_WORKER_DEBUG=1` to show debug messages:

```
pcons worker: running directly (the action wants /a/.venv, this worker is /b/.venv)
```

It also stops the client discarding the worker's own stderr, which can
help debug a worker that will not start.

## What pcons guarantees in return

- The socket path is short enough for `AF_UNIX` (about 104 bytes), stable
  across builds for the same `Worker`, and inside a directory only the user
  can enter.
- The start command runs detached, with its output discarded. A worker normally can't output to the terminal.
- Failing to reach a worker is never fatal, so a worker may be missing, broken, or slow to start without breaking a build.
