# NoteRedis

A Redis client as a Jupyter kernel.

Each cell holds one or more Redis commands, and the output matches `redis-cli`
byte-for-byte in `text/plain`. Richer `text/html` / `application/json`
representations layer on top of that, so `HGETALL` renders as a table without
ever losing the plain text a support engineer would paste into a ticket.

`redis-cli` is transient; a notebook is a saveable, diffable, re-runnable
runbook.

## Requirements

- Python 3.10 or newer
- A Redis server to talk to (Redis 8 is what this is developed against)
- JupyterLab, Jupyter Notebook, or any other Jupyter frontend

## Installation

From a checkout:

```bash
pip install -e '.[dev]'      # or: uv sync
redis-kernel-install --user  # or: python -m redis_kernel.install --user
```

Check it took:

```bash
jupyter kernelspec list      # should list "redis"
```

The kernel then appears as **Redis** in JupyterLab's launcher and in the
*New* / *Change kernel* menus.

Use `--sys-prefix` instead of `--user` to install into the current environment
(handy in a container or a shared checkout), or `--prefix DIR` for somewhere
specific.

**Why the separate step.** Installing the package registers no kernel by
itself — deliberately. The interpreter path isn't known until install time, so
the spec inside the package is a template, and the installer is what writes a
real one with `sys.executable` in its `argv`. Shipping a spec that said `python`
would leave it resolved against whatever `PATH` the Jupyter *server* has: right
when the server and the kernel share an environment, and a puzzling
`No module named redis_kernel` when they don't. Installing the template directly
fails immediately instead, and says what to run.

## Connecting

State the connection in the notebook, in the first cell, with `%connect`:

```redis
%connect -h prod-cache.internal -p 6380 --tls --insecure --user support --askpass -n 0 -3
%status
```

```
server:   prod-cache.internal:6380
db:       0
protocol: RESP3
tls:      on (certificate check disabled)
user:     support
password: (set)
version:  8.2.1
mode:     standalone
```

The flags are `redis-cli`'s own, so there is nothing new to learn:

| Flag | Meaning |
| --- | --- |
| `-h HOST` / `-p PORT` | server host and port |
| `-n DB` | database number |
| `-s PATH` | unix socket instead of host/port |
| `-u URL` | a `redis://` or `rediss://` URL; later flags override its parts |
| `-2` / `-3` | RESP protocol version (default: RESP3) |
| `--user NAME` | ACL username |
| `--pass PASS` / `-a PASS` | password |
| `--askpass` | prompt for the password instead of writing it down |
| `--tls` | connect over TLS |
| `--insecure` | TLS without validating the server certificate |
| `--cacert FILE` / `--cacertdir DIR` | CA to verify the server against |
| `--cert FILE` / `--key FILE` | client certificate and key, for mTLS |

`%help connect` lists the same flags inside a notebook, so this page is not the
only place they are written down.

`--insecure`, `--cacert`, `--cert` and `--key` each imply `--tls`: connecting in
the clear to someone who asked for a secure connection is the worse failure.
`--sni` and `--tls-ciphers` are real `redis-cli` flags that redis-py cannot
honour, and say so rather than being ignored.

### Keeping the password out of the notebook

A notebook gets committed, so there are two ways to connect without writing the
secret into the file:

```redis
%connect -h prod-cache.internal --tls --user support --askpass
```

`--askpass` prompts through Jupyter's stdin channel — JupyterLab shows a masked
box — and the value never reaches the cell source, the cell output, or the
environment. Under `nbconvert` or `papermill`, where there is nothing to prompt
with, it fails with an error rather than hanging.

```redis
%connect -h prod-cache.internal --tls --user support --pass ${REDIS_PASSWORD}
```

Any flag value expands `$VAR` and `${VAR}` from the kernel's environment, so the
notebook records the variable name and not the secret. This form works
unattended. An unset variable is an error, not an empty string.

Either way, `%connect` and `%status` report `password: (set)` and never the
value, because that output is saved into the notebook too.

### Defaults, and re-running cells

With no `%connect`, the kernel connects on the first command to `$REDIS_URL`,
defaulting to `redis://localhost:6379/0`:

```bash
REDIS_URL=redis://cache.internal:6379/2 jupyter lab
```

**Each `%connect` states the whole connection.** Anything left out falls back to
`REDIS_URL` and the built-in defaults — never to whatever a previous `%connect`
set up. So `%connect -h other` after a TLS connection is *not* a TLS connection,
and re-running a cell always connects the same way it did the first time. That
matters more for a runbook than the convenience of tweaking one flag at a time.

In a runbook that must only ever talk to the server it names, turn the
autoconnect fallback off:

```redis
%config autoconnect off
```

A command that runs before `%connect` then fails with `(error) not connected.
Run %connect first`, instead of quietly dialling `localhost`.

If the target turns out to be running in cluster mode, the kernel switches to a
cluster client and follows `MOVED`/`ASK` redirects the way `redis-cli -c` does.
(One fidelity caveat: redis-py discards the custom RESP parser on that path, so
a cluster connection is parsed by redis-py's own parser.)

## Using it

A cell is a list of commands, one per line. Blank lines and `#` comments are
skipped:

```redis
# warm up a hash
HSET user:1 name "Ada Lovelace" role admin
HGETALL user:1
```

```
(integer) 2
1) "name"
2) "Ada Lovelace"
3) "role"
4) "admin"
```

Things worth knowing:

- **Errors don't stop the cell.** A bad command prints `(error) …` and the next
  line still runs — the behaviour of interactive `redis-cli`, not
  `redis-cli < file`. The cell as a whole still succeeds; a failed *cell* means
  the kernel or the connection broke, not that Redis rejected a command.
- **Quoting is shell-like.** Arguments are split with `shlex`, so
  `SET greeting "hello world"` does what you expect. An unbalanced quote leaves
  the line incomplete, so Enter continues it instead of executing.
- **Session state survives cells.** `SELECT`, `HELLO 3`, and an open `MULTI`
  all carry over to the next cell, because the client pins a single connection.
  A cell that ends mid-transaction stays in the transaction; the next cell's
  commands keep coming back `QUEUED` until you `EXEC` or `DISCARD`.
- **Tab completes commands**, then subcommands (`CONFIG GET`) and known
  argument tokens, from a table built once from `COMMAND DOCS`. Shift-Tab shows
  a command's summary, arity, flags, and since-version.
- **Key-name completion is off by default.** Turn it on with
  `%config complete_keys on` and Tab completes key names at the argument
  positions that actually take a key — `GET <tab>`, `MSET k v <tab>`,
  `OBJECT ENCODING <tab>` — but not at a value position, and not for commands
  with no positional keys. It uses a bounded `SCAN` (at most 10 calls, 50
  matches, `COUNT 100`), never `KEYS`, and never runs inside a `MULTI`, where
  the `SCAN` would be queued into your transaction instead of answered. It is
  opt-in because it is real load against what may be a production keyspace.
  Keys that could not be inserted verbatim — binary keys, or keys containing
  spaces or quotes — are skipped, since a completion is substituted literally.

### Magics

Magics handle the things that aren't Redis commands. They take the same
one-per-line form as commands, and a misused magic prints `(error) …` just like
a bad command does.

| Magic | What it does |
| --- | --- |
| `%connect [url] [flags]` | Open a connection, replacing the current one. See [Connecting](#connecting). Resets `db` and clears any open transaction. |
| `%status` | Server, db, RESP protocol, TLS, ACL user, server version and mode. Before connecting, names the target it would use. |
| `%select <db>` | Shorthand for `SELECT`. |
| `%protocol [2\|3]` | Show the RESP version, or reconnect using the other one, keeping the rest of the connection as it is. |
| `%config [name [value]]` | Kernel settings: `complete_keys`, `autoconnect`, `render`. Unrelated to the `CONFIG` command. |
| `%render [rich\|plain]` | Rendering mode for the rest of this cell only. See [Rich output](#rich-output). |
| `%help [magic]` | List the magics, or explain one of them in full. |

### Rich output

Some replies get a richer representation added alongside the text:

| Command | Extra representation |
| --- | --- |
| `HGETALL` | field/value table |
| `CONFIG GET` | parameter/value table |
| `XINFO STREAM` | property/value table |
| `XRANGE`, `XREVRANGE` | one row per entry, one column per field |
| `INFO` | a table per section |
| `JSON.GET`, `JSON.MGET`, `JSON.ARRPOP` | `application/json`, so JupyterLab shows a collapsible tree |
| `FT.SEARCH` | one row per document, one column per field (`WITHSCORES`/`WITHPAYLOADS` add columns) |
| `FT.AGGREGATE` | one row per group, one column per field |
| `FT.INFO` | property/value table, plus a field table on RESP3 |
| `MEMORY STATS`, `BF.INFO`, `CF.INFO` | metric/value table |
| `ZRANGE` and friends, `ZPOPMIN`/`ZPOPMAX`, `HRANDFIELD` | member/score (or field/value) table, when the reply carries one |
| `XINFO GROUPS`, `XINFO CONSUMERS` | one row per group/consumer, one column per property |
| `SLOWLOG GET` | one row per entry: id, timestamp, duration, command, client |
| `LATENCY HISTORY`, `LATENCY LATEST` | one row per sample/event |
| `CLIENT INFO` | property/value table |
| `CLIENT LIST` | one row per client, one column per property |
| `TS.RANGE`, `TS.REVRANGE` | one row per sample |

`text/plain` is always the exact `redis-cli` text, in every mode — a renderer
only ever *adds* to the bundle. A renderer handed a reply shape it doesn't
expect adds nothing and the plain text stands alone, so a rendering bug can
never cost you your output.

Switch it off for the session, or for one cell:

```redis
%config render plain     # rest of the session: redis-cli text only
%render rich             # rest of this cell only, then back to the setting
```

`plain` is worth having for a runbook you commit: HTML tables inflate the
`.ipynb` and make `git diff` noisy, and consecutive commands in one cell stay
in a single contiguous block of output rather than becoming separate blocks.

The HTML carries no colours, fonts or sizes — Jupyter already styles tables in
output, per theme, and a palette of ours would fight it in half of them.

### RESP3

RESP3 is the default protocol for a new connection. The formatter follows:
maps render as `1# "k" => "v"`, and sets, doubles, booleans, big numbers, and
verbatim strings each get their `redis-cli` RESP3 rendering.

`%connect -2`, `%protocol 2`, or a plain `HELLO 2` switches back to RESP2 —
useful against a server too old to speak RESP3, or to match a runbook that
was written against RESP2 output.

### Blocking and streaming commands are out of scope

`SUBSCRIBE`, `PSUBSCRIBE`, `SSUBSCRIBE`, `MONITOR`, `PSYNC`/`SYNC`, and the
zero-timeout forms of `BLPOP`, `XREAD BLOCK 0` and `WAIT` never return on their
own. This kernel **deliberately does not support them**:

```redis
SUBSCRIBE news
```

```
(error) SUBSCRIBE never returns on its own and is not supported by this kernel. Use redis-cli to watch a live feed
```

A runbook is a sequence of commands with answers; watching a live feed is a
different job, and `redis-cli` is still the right tool for it. Refusing keeps
the kernel answering — running one would wedge it until you restarted it.

A blocking command with a *real* timeout is left alone, because it comes back
by itself: `BLPOP queue 5` runs exactly as it would in `redis-cli`. Only the
zero-timeout — wait forever — forms are refused.

## Development

```bash
pip install -e '.[dev]'                   # or: uv sync
pytest                                    # protocol tests skip without a server
ruff check . && ruff format --check . && mypy src
```

Most of the suite runs against canned replies and needs nothing. The protocol
tests (`jupyter_kernel_test` driving a real kernel process over ZMQ) need a
server, and skip themselves when there isn't one:

```bash
docker run -d -p 6379:6379 redis:8
REDIS_URL=redis://localhost:6379/0 pytest
```

They install their kernelspec into a temporary directory, so running the tests
never puts a kernel in your launcher.

Two rules for anything added here:

- Unit tests must not depend on `REDIS_URL` being set or unset, since settings
  fall back to it by design and CI points it at a service container.
- A test that needs a server must skip, not fail, without one — except in CI,
  where `REDIS_KERNEL_REQUIRE_SERVER=1` turns that skip into a failure so a
  broken service container can't pass as green.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs lint, format
and `mypy` once, then the suite on Python 3.10, 3.12 and 3.13 against a
`redis:8` service container.

Formatter expectations are not hand-written — they are captured from a real
`redis-cli`. To regenerate the corpus after adding a payload to
`tests/capture/payloads.py`:

```bash
python tests/capture/generate.py          # needs the redis-cli binary, no server
```

`formatter.py`, `client.py`, `commands.py` and `magics.py` must stay importable
without `ipykernel`, so a `%%redis` cell magic could reuse them later. Anything
needing the kernel — prompting for a password over the stdin channel, say — is
passed in from `kernel.py` rather than reached for directly. See
[CLAUDE.md](CLAUDE.md) for the architecture decisions and the rules the output
formatting has to obey.

## License

See [LICENSE](LICENSE).
