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

Blocking and streaming commands

SUBSCRIBE, PSUBSCRIBE, MONITOR, XREAD BLOCK 0, BLPOP, WAIT never return on their own. Run the command on a worker thread and flush iopub stream messages as replies arrive; implement do_interrupt (and a SIGINT path for kernels launched without message-based interrupt) to stop the loop and close the connection cleanly. Never block the shell channel waiting on one of these.

This is the main thing that separates a usable kernel from a toy. Treat it as a first-class feature, not a follow-up.

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
Streaming/blocking commands and interrupt.
COMMAND DOCS table → do_complete, do_inspect.
do_is_complete and MULTI state.
Rich renderers, starting with HGETALL, XRANGE, and INFO.
References
Kernel protocol: https://jupyter-client.readthedocs.io/en/stable/messaging.html
Wrapper kernels: https://jupyter-client.readthedocs.io/en/stable/wrapperkernels.html
jupyter_kernel_test: https://github.com/jupyter/jupyter_kernel_test
Prior art worth reading: bash_kernel (pexpect approach, and why we didn't), jupysql (magic approach, rich rendering)
Output rules: redis/src/redis-cli.c, redis/src/cli_common.c