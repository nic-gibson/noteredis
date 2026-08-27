redis-kernel

A Jupyter kernel that behaves like redis-cli: each notebook cell contains one or more Redis commands, output matches redis-cli byte-for-byte in text/plain, with richer text/html / application/json representations layered on top.

Why this exists

redis-cli is transient. A notebook is a saveable, shareable, re-runnable runbook. The point of the kernel (rather than a plain shell transcript) is:

Support/triage runbooks that can be committed, diffed, and handed to a customer.
Rich rendering: HGETALL/XRANGE/FT.SEARCH as tables, JSON.GET as a JSON tree, INFO/MEMORY STATS/LATENCY HISTORY as charts.
Teaching material where commands and their output live together.

If a change makes the kernel more convenient but breaks text/plain fidelity with redis-cli, prefer fidelity.

Architecture decision (already made)

Native Python wrapper kernel. Subclass ipykernel.kernelbase.Kernel (NOT IPythonKernel) and talk to the server through redis-py.

Rejected alternatives, do not revisit without a reason:

pexpect wrapping the redis-cli binary. Gets output formatting for free but prompt detection is brittle, MONITOR/SUBSCRIBE streaming is painful, and there's no clean path to structured output for rich rendering.
%%redis IPython cell magic only. Gets Python interop for free and is ~50 lines, but no dedicated completions, no syntax highlighting, no entry in the New menu. This may still ship later as a secondary package sharing the formatter and client modules — keep those two modules free of kernel-protocol imports so that stays possible.

redis-py is constructed with decode_responses=False. All decoding and escaping happens in the formatter so binary-safe values render the way redis-cli renders them.

Layout
pyproject.toml
src/redis_kernel/
  __init__.py
  __main__.py        # ipykernel_launcher entry point
  kernel.py          # Kernel subclass: do_execute/do_complete/do_inspect/do_is_complete
  formatter.py       # RESP reply -> redis-cli text  (NO kernel imports)
  client.py          # connection/session state      (NO kernel imports)
  magics.py          # %connect, %status, %select, %protocol
  render/            # optional rich mimebundle renderers, keyed by command name
  kernelspec/
    kernel.json
    logo-64x64.png
    logo-32x32.png
tests/
  test_formatter.py  # pure unit tests, no server needed
  test_protocol.py   # jupyter_kernel_test against a live redis:8 container

formatter.py and client.py must stay importable without ipykernel.

Commands
bash
uv sync                                   # or: pip install -e '.[dev]'
jupyter kernelspec install --user src/redis_kernel/kernelspec --name redis
pytest tests/test_formatter.py            # fast, no server
docker run -d -p 6379:6379 redis:8
pytest                                    # full suite
ruff check . && ruff format --check . && mypy src

REDIS_URL (default redis://localhost:6379/0) selects the server for tests and for the kernel's initial connection.

Implementation notes
do_execute

Split the cell on newlines; skip blanks and # comments; shlex.split each line; one execute_command per line. Errors are caught per line, printed as (error) …, and execution continues — matching interactive redis-cli, not redis-cli < file. Return status: 'ok' even when a command errored; reserve status: 'error' for kernel faults, not Redis error replies.

Reply formatting — the bulk of the fiddly work

formatter.py reimplements redis-cli's output rules. Read redis/src/redis-cli.c and cli_common.c for the exact behaviour rather than inferring it from memory. Cases that have bitten people before:

(integer) 1, (nil), (empty array), (empty hash), OK bare
bulk strings always double-quoted with C-style escapes (\n, \xac) for non-printables
nested arrays get 1) 1) "x" index prefixes, right-aligned to the widest index at each depth
RESP3: maps (1# "k" => "v"), sets, doubles ((double) 1.5), booleans, big numbers, verbatim strings, push messages
(x.xxs) latency suffix is a redis-cli flag, not default — do not emit it

Every rule above gets a test in test_formatter.py with the expected string copied from a real redis-cli run. Do not hand-write expectations.

Blocking and streaming commands — out of scope

SUBSCRIBE, PSUBSCRIBE, MONITOR, XREAD BLOCK 0, BLPOP 0, WAIT 0 never return on their own. Streaming them on a worker thread was the original plan; it is now a deliberate non-goal. Pub/sub, MONITOR and stream tailing are not what this kernel is for — a runbook is a sequence of commands with answers, and watching a live feed is what redis-cli is still good at.

What is required is that they never wedge the kernel. is_blocking() in client.py detects them, and do_execute refuses them with an (error) line and carries on to the next command. Never block the shell channel waiting on one of these. A blocking command with a non-zero timeout is left alone: it returns on its own, exactly as it would in redis-cli.

Do not add worker threads, an interrupt loop, or a streaming iopub path without agreeing to reverse this decision first.

Session state

Held on the kernel/client object, not in the connection pool:

current db (SELECT), reset on reconnect
MULTI queuing: while in a transaction, replies are QUEUED; EXEC/DISCARD end it; a cell ending mid-transaction leaves the state open for the next cell
RESP2 vs RESP3 (HELLO 3), which changes formatter behaviour
%connect redis://… swaps servers; also handles TLS (rediss://), ACL user/password, and RedisCluster when the target is a cluster (follow MOVED/ASK like redis-cli -c)
%status prints host/port/db/protocol/server version
Completions and inspection

Build a command table at startup from COMMAND DOCS (fall back to COMMAND INFO on servers that lack it). That single call powers both:

do_complete: command names at position 0, then subcommands and known argument tokens
do_inspect: arity, flags, summary, and since-version on Shift-Tab

Key-name completion via SCAN MATCH prefix* COUNT 100 is off by default and gated behind a %config toggle — it is a real load generator against a production keyspace, and the kernel must never issue KEYS.

do_is_complete

Return incomplete for unbalanced quotes and for an open MULTI, so Enter continues the line instead of executing. Everything else is complete.

Rich output

do_execute sends display_data with a mimebundle: text/plain is always the exact redis-cli rendering, and richer types are additive. Renderers live in render/, keyed by command name, and each one must degrade to plain text if the reply shape is unexpected — a renderer bug must never lose the user's output.

Testing
test_formatter.py — table-driven, expectations captured from real redis-cli output. This is where most of the coverage lives.
test_protocol.py — jupyter_kernel_test.KernelTests subclass exercising execute, completion, is_complete, and interrupt against a launched kernel and a redis:8 container. CI runs it as a service container.
Build order
formatter.py + its tests. No kernel, no server. Get RESP2 exactly right.
client.py — connection, session state, %connect/%status.
kernel.py do_execute for non-blocking commands; kernelspec; install and use it.
RESP3 in the formatter, with HELLO 3 wired through.
COMMAND DOCS table → do_complete, do_inspect.
do_is_complete and MULTI state.
Rich renderers, starting with HGETALL, XRANGE, and INFO.

All of it is done. (Streaming was step 5 and has been dropped; see above.)

Renderers so far: HGETALL, CONFIG GET, XINFO STREAM, XRANGE/XREVRANGE, INFO, JSON.GET as application/json, and FT.SEARCH/FT.AGGREGATE. Toggled with %config render rich|plain, or %render for one cell. Candidates not yet written: MEMORY STATS, LATENCY HISTORY, XINFO GROUPS/CONSUMERS. Charts, where they make sense, are still unexplored — everything so far is a table.

Installing the kernelspec

redis_kernel/kernelspec/kernel.json is a template, not an installable spec: nothing knows the interpreter path until install time. Its argv[0] is the placeholder PYTHON-SET-BY-redis-kernel-install, and install.py replaces it with sys.executable. That is the only supported route: redis-kernel-install --user, or python -m redis_kernel.install.

The wheel intentionally has no shared-data entry, so pip install registers nothing. Do not add one back, and do not tell people to run jupyter kernelspec install on the packaged directory -- both paths install a spec naming an interpreter nobody chose. The placeholder makes that fail loudly rather than working in one environment and breaking in the next; kernel_json() also refuses to run if the template stops carrying it.

CI

.github/workflows/ci.yml: ruff check, ruff format --check and mypy src once, then pytest on 3.10/3.12/3.13 against a redis:8 service container. All three gates pass; keep them passing.

Tests that need a server skip without one, which would let a broken service container report green -- so CI sets REDIS_KERNEL_REQUIRE_SERVER=1, which turns that skip into a failure.

The captured corpus is deliberately not regenerated in CI as a freshness check: it records the exact redis-cli version it came from, so any other version produces a different file for reasons unrelated to correctness.
References
Kernel protocol: https://jupyter-client.readthedocs.io/en/stable/messaging.html
Wrapper kernels: https://jupyter-client.readthedocs.io/en/stable/wrapperkernels.html
jupyter_kernel_test: https://github.com/jupyter/jupyter_kernel_test
Prior art worth reading: bash_kernel (pexpect approach, and why we didn't), jupysql (magic approach, rich rendering)
Output rules: redis/src/redis-cli.c, redis/src/cli_common.c