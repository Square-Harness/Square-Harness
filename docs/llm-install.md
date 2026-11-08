# Install Square Harness (instructions for an AI agent)

You are a coding agent with shell access. A user asked you to install Square
Harness by following this file. Do the whole installation yourself, check every
step, and end with the harness running and its web-app address given to the user.
For what the harness is, see [guide.md](guide.md).

There are three plans. Self-hosting is the primary setup; the hosted API plan is
an optional way to test the harness without a GPU. Choose the plan requested in
step 1.

- **Plan A, local:** the model runs on this computer with Ollama. Use it for a
  quick test with a tiny Qwen (any computer, even without a GPU), or for real use
  on a gaming GPU or an Apple Silicon Mac. On Linux with an NVIDIA GPU of 24 GB
  or more, HyperQwen can replace Ollama.
- **Plan B, cluster:** the model runs on a GPU of a computing cluster or a remote
  GPU server reached by SSH. The harness and its web app run on this computer and
  reach the model through an SSH tunnel. The model server is HyperQwen, the
  team's tested setup (Qwen 3.8 27B with 4-bit weights and speculative
  decoding), with fallbacks.
- **Plan C, API trial only:** the harness and web app run on this computer, and
  DeepInfra serves DeepSeek V4.1 Flash 1017. Use only when the user explicitly
  agrees to send model prompts and selected source text to the provider. No
  GPU, SSH tunnel or Cloudflare account is needed for this local interface test.
  Follow the hosted-API section below after installing the harness.

## Rules

- **Ask once.** Before installing anything, ask the user, in a single message,
  every question in step 1 whose answer you cannot determine yourself. Then work
  without interrupting them, except for a password or a decision these rules
  reserve for the user.
- **No `sudo` without asking.** Prefer user-space installs (`~/.local`, the
  repository's `.venv`). If only `sudo` can fix something, ask the user, giving
  the exact command and the reason.
- **Keep ports private.** Never bind a model server or the web app to a public
  interface, and never disable SSH host-key checking. Model servers listen on
  `127.0.0.1` and are reached through SSH tunnels.
- **Keep secrets out of shared places.** The access token is in the `Open:`
  address, in the web app's log (kept in the repository's git-ignored `.local/`)
  and in `<gui-root>/.mathagent/gui-token` (the workspace when `--gui-root` is not
  given). Give the address to the user, but never commit it, paste it into an
  issue, or copy it anywhere else.
- **Never pass `--online` together with a remote `--host`** unless the user agrees
  to send their prompts and files to that machine. With an SSH tunnel, `--online`
  is not needed for the model.
- **Don't guess around a failed check.** When a check fails, use the fallback
  given here, or the matching entry in [Troubleshooting](#troubleshooting).
  Report anything you could not fix.
- **Variables:** keep them in `<repo>/.local/square.env` (git ignores `.local/`),
  and start every command with `cd <repo> && . .local/square.env`. Shell
  variables do not survive between an agent's separate commands.
- **Background processes:** run long-lived ones (model server, web app, tunnel)
  in the background, with output going to a log file. Find them later by a
  pattern that includes an absolute path or a port, and check the command line
  before you signal a PID:
  - `pgrep -af` also lists your own shell's command line;
  - `pkill -f <pattern>` can match your own shell and kill it.

## 1. Facts and decisions

Run these commands and keep the answers:

```bash
uname -sm; cat /etc/os-release 2>/dev/null | head -2; sw_vers 2>/dev/null
echo "VIRTUAL_ENV=${VIRTUAL_ENV:-none}"
for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do q=$(command -v $p) && echo "$p -> $q: $($q --version 2>&1)"; done
command -v git uv ollama docker latexmk zstd
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1 | head -4
free -g 2>/dev/null | head -2; sysctl -n hw.memsize 2>/dev/null; df -h ~ | tail -1
```

If `VIRTUAL_ENV` is set, another virtual environment is active, and its Python
shadows the system's. Use Python by absolute path (for example
`/usr/bin/python3.12`), outside that environment.

Decide (ask the user only what you cannot tell):

1. **Plan.** Choose C if the user requested the hosted DeepInfra API. Otherwise
   choose B if the user mentioned a cluster, Slurm, SSH or a remote GPU.
   Choose A otherwise. In Plan A, on Linux with an NVIDIA GPU of 24 GB or more,
   ask whether the user wants HyperQwen
   ([A4](#a4-optional-hyperqwen-instead-of-ollama)), the server the team runs
   on its cluster, instead of Ollama. It is faster, but it needs Docker, and the
   team has not tested it on a desktop GPU. Ollama stays the default.
2. **Workspace**: the folder for the user's research files and saved jobs.
   Default: `~/research/square-workspace`.
3. **Plan A: model size**, from the table in [step A2](#a2-choose-the-model).
   Without a GPU or a working NVIDIA driver, take the tiny test model and tell the
   user that its answers only show that the software works.
4. **Plan B**: the SSH login (`user@login-host`, and any jump host), whether the
   cluster uses Slurm (and which GPU partition), and the GPU type. Ask the user;
   then confirm with the commands in [step B1](#b1-inspect-the-gpu-machine).
5. **Optional parts** ([step 4](#4-optional-parts)): the Python sandbox (needs
   Docker), compiled write-ups (needs `latexmk`), and search keys.

Requirements: Linux or macOS (native Windows is not supported); Python 3.10 or
newer; git; about 100 MB of disk for the harness, plus the model (1–20 GB with
Ollama, about 40 GB with HyperQwen). Optional:

- Docker, for the Python sandbox;
- a TeX distribution with `latexmk` (and BibTeX or Biber, which TeX Live and
  MacTeX include), to compile write-ups and their bibliographies;
- `zstd`, to unpack Ollama without `sudo`.

## Hosted tester deployment

The ordinary GUI is a trusted single-workspace service. Before sharing it with
testers, give each person a separate Linux service account, private home and
workspace, model credential and rootless sandbox runtime. Keep backend listeners
on loopback and their service configuration and credentials outside all served
workspaces. Use separate backend ports and `--gui-root` fixed to each workspace.
LaTeX compilation retains paranoid file access and disabled shell escape.

The optional entry point is a Cloudflare Tunnel to the local tester gateway,
protected by an Access self-hosted application and an exact email allowlist.
Configure email-code authentication. Install the gateway dependency separately:

```sh
python -m pip install -r deployment/requirements-testers.txt
python deployment/tester_gateway.py --help
python deployment/tester_gateway.py --config /etc/square-harness/gateway.json
```

Its private JSON configuration contains `hostname`, the exact HTTPS Cloudflare
Access `issuer`, the application's `audience`, and `tenants`: a mapping from each
invited email to a fixed `port` and absolute `token_file`. Configuration and token
files must be mode 0600 or 0640, readable only by the gateway account. Never put
the model key there. The gateway binds to `127.0.0.1:8790`; publish that listener
through Tunnel, with no public backend ports. Copy existing mail DNS records
before moving nameservers, and migrate DNSSEC rather than leaving an old DS key.
Do not cache private app responses or enable prompt/body logging.

Onboarding must provision the private backend, workspace, sandbox and allowance,
then add the same identity to the gateway's `tenants` mapping and Access email
allowlist. An operator's one-email helper may automate these steps; it is
deployment-specific, not part of Cloudflare email-code authentication.

The authenticated gateway endpoint `GET /api/account` returns only the verified
email and the configured application's same-origin Access logout URL. It never
returns a JWT, provider key or workspace token. The frontend uses this optional
endpoint for its account menu; ordinary local backends return 404 and keep the
local interface. A public presentation website and a protected app subdomain can
share a domain while only the app is routed through this gateway.

For a capped Standard-service tester, configure `SH_DEEPINFRA_QUOTA_FILE` as an
absolute file outside served workspaces, inside a tenant-owned mode-0700 directory.
The created SQLite file is mode 0600. `SH_DEEPINFRA_QUOTA_USD` defaults to `2.50`;
`SH_DEEPINFRA_QUOTA_INPUT_PER_MILLION` and
`SH_DEEPINFRA_QUOTA_OUTPUT_PER_MILLION` default to conservative `0.20` and `0.60`.
These settings are immutable for an existing ledger. Pair this with a separately
model-restricted provider credential; reuse credentials and ledgers on restart.
Priority is refused for a quota-enabled instance. An exhausted allowance is an
operational stop and does not authorize another allowance automatically.

For a $2 pilot cap, set `SH_DEEPINFRA_QUOTA_USD=2` explicitly in each new tester's
service configuration. Keep exceptions in private operator configuration. Changing
this variable alone does not update an existing ledger: an approved cap change
requires an administrative migration of that same ledger and matching service
settings, preserving all charged and reserved amounts. Never replace a ledger
or mint a new credential to simulate a cap increase.

To bound heavy simulations across accounts, create a trusted shared directory
with mode 0750, owned by root and a dedicated numerical group. Pre-create
`slot-0.lock` and `slot-1.lock` as root-owned mode-0660 files in that group; tenants
must not be able to replace directory entries. Add only sandbox service accounts
to the group and configure `SH_NUMERICS_SLOTS_DIR` and `SH_NUMERICS_SLOTS=2` in their
service environment. Arrange recreation at boot. This supplements per-container
memory/CPU limits; it does not change model or Proof budgets.

### Optional feedback delivery

Feedback works without Slack: reports are saved privately and the browser says
when delivery is unconfigured. For report text, native screenshots and a report
ZIP delivered in one private Slack thread, install a bot with `chat:write` and
`files:write` and add it to the feedback channel. Save its bot token through a
hidden prompt into a private mode-0600 regular file. Set
`SH_FEEDBACK_SLACK_TOKEN_FILE` to its absolute filename and
`SH_FEEDBACK_SLACK_CHANNEL` to the fixed private channel ID in the service
environment. Each backend user must own its credential file. No message-history
or file-read permission is needed. Never put the token in Git, a command
argument, the served workspace or frontend configuration.

Existing installations may retain `SH_FEEDBACK_WEBHOOK_FILE` for summary-only
incoming webhooks. A configured bot takes precedence; an invalid bot setup does
not fall back to the webhook. Delivery is confirmed only after Slack acknowledges
the parent message and every attachment. A partial upload keeps the private
report and its delivery progress for inspection.

`SH_FEEDBACK_DIR` may select a mode-0700 directory outside the served workspace,
owned by that backend user. The default is the protected `.mathagent/feedback`
folder. Reports and ZIPs have mode 0600. A report is durable before notification;
Slack failure does not delete it. Screenshots are limited to three PNG/JPEG/WebP
files of 3 MiB each; consented diagnostic traces are limited to 32 MiB.

For hosted downloads, add a `feedback_admins` list of existing invited maintainer
emails to the private gateway configuration. It defaults to no administrators.
Set `SH_FEEDBACK_REPORT_BASE_URL` in each backend to the protected application
origin followed by `/api/admin/feedback/{tenant}`. The server replaces `{tenant}`
with a hash of the gateway-verified reporter identity; ordinary testers cannot
use these cross-account archive links. They can download their own submitted
report through their normal authenticated backend. Keep the gateway and backend
release compatible when enabling this route. Local launches may instead use an
HTTP loopback prefix ending in `/api/feedback`; these links work only in the
paired local browser.

The isolated API launcher accepts `--workspace` for a separate acceptance
workspace and `--online` to enable literature tools. Without `--online` it keeps
its previous locked-offline behavior. If the private
`.local/feedback/slack-webhook` file exists, the launcher uses it for feedback
unless `SH_FEEDBACK_WEBHOOK_FILE` was explicitly configured. For direct file
delivery it similarly loads `.local/feedback/slack-bot-token` and the channel ID
in `.local/feedback/slack-channel`, unless their environment settings were
explicitly configured. It sets no model
or Proof-budget changes for feedback or projects.

### Updating a hosted pilot

Keep application code separate from each tester's persistent workspace and
private service configuration. Before an update, record the deployed revision
and preserve a private backup of workspaces, image caches and configuration,
plus a consistent SQLite backup of each quota database. Use an idle window or
explicitly pause affected work before
restarting a backend. An in-flight generation can be interrupted by a restart;
its unfinished output may be lost and its unreported-usage reservation retained.
Do not automatically replay a request to hide that interruption.

For a Git-backed installation, inspect local changes and the incoming release
before updating. Use a fast-forward when available; never overwrite local
configuration with a hard reset. Deploy the matching backend, gateway and built
frontend revision together, and reuse the existing workspace paths, service
accounts, credentials and allowance ledgers. A code update does not authorize
new spending limits, changed Proof budgets or edits to saved job JSON.

After restarting the affected services, check their status and a maintainer's
email-code login. Confirm the account identity, existing history and files,
allowance meter and access isolation before inviting testers back. Inspect a
saved paused job without granting it a new allowance. Keep the previous code
revision available for rollback; do not roll back user data by replacing it
with an older workspace or database.

## 2. Install the harness

Run these steps in both plans, on the computer where the user will open the web app.

```bash
git clone https://github.com/Square-Harness/Square-Harness.git ~/Square-Harness   # or any folder the user prefers
cd ~/Square-Harness
```

If the current directory already contains the repository (it has `mathagent/`
and `pyproject.toml`), use it instead of cloning. Write the variables file, with
`PY` set to the absolute path of the newest Python ≥ 3.10 found in step 1:

```bash
mkdir -p .local
cat > .local/square.env <<EOF
REPO=$PWD
PY=/usr/bin/python3.12
WS=$HOME/research/square-workspace
EOF
. .local/square.env && mkdir -p "$WS"
```

The web app refuses a workspace that does not exist. Then create the virtual
environment:

```bash
cd "$REPO" && "$PY" -m venv .venv && .venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[research]'
```

Fallbacks:

- `ensurepip is not available` or `No module named venv` (Debian/Ubuntu without
  `python3-venv`): the failed command leaves a partial `.venv`. Run
  `rm -rf .venv`, then `"$PY" -m venv --without-pip .venv`, then
  `curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python`. Then run
  the `pip install -e` line.
- No Python 3.10+ (common on cluster login nodes and older macOS): install uv in
  user space with `curl -LsSf https://astral.sh/uv/install.sh | sh`. Then run
  `~/.local/bin/uv venv --python 3.12 .venv` and
  `~/.local/bin/uv pip install --python .venv/bin/python -e '.[research]'`.
- No network access to PyPI: the only runtime dependencies are `rich`,
  `prompt-toolkit` and, for PDFs, `pypdf`. The harness also runs without them,
  with a plainer terminal and no PDF reading.

Check:

```bash
.venv/bin/square-harness --version        # prints: Square Harness 1.0.0
```

If this fails with `ModuleNotFoundError: mathagent`, run the harness as
`PYTHONPATH=$REPO .venv/bin/python -m mathagent` from the repository instead.
Every command below accepts that form in place of `.venv/bin/square-harness`.

## Plan C: DeepInfra API with a local interface

Create an account at https://deepinfra.com/login, then create a named key at
https://deepinfra.com/dash/keys. The setup helper defaults to the key name
`square-harness-local`. Enable `inference`, `models:read` and
`account_scoped_jwt`; the last permits creation of the restricted test
credential. Check Billing for the available balance; use a manual top-up only
with the user's agreement and leave automatic top-up disabled. Do not assume
signup credits or a particular minimum payment.

Enter the parent key only through the hidden terminal prompt:

```bash
python3 scripts/setup-deepinfra.py
```

The parent key is not saved. The helper creates a DS4-only, 24-hour scoped JWT
with a $1 allowance and stores it at `.local/deepinfra/credential`, mode 0600,
inside private directories. Use `--key-name NAME` if the dashboard key has a
different name. An existing credential prevents minting another allowance.
`--replace` explicitly creates a new allowance; it does not revoke the old JWT.
The provider's spending limit is distinct from workflow token limits; ongoing
requests may settle after admission. Changing parallel work does not mint a
new credential or increase its existing spending allowance.

Native image input needs this pinned **DS4.1 Flash 1017** DeepInfra profile;
there is no extra dependency or vision service to install. Keep the private
`.mathagent/images` cache when moving or backing up a workspace, alongside its
saved job state. The GUI uses authenticated same-origin thumbnail requests.
PNG, JPEG and static WebP are accepted, with eight images per conversation/job,
8 MiB per image and 24 MiB combined, at most 8,192 pixels per side and 32
megapixels. Image references remain bound to their original conversation turn,
including after a reload or resume; text-only replies add no new attachment.
The Qwen profile rejects native images.
For terminal workflows, include image paths in `--proof-file` or
`--research-file` just like text sources.

```bash
python3 scripts/start-deepinfra.py --profile ds4
```

This opens the local GUI on port 8780, uses the private workspace
`.local/deepinfra/workspace`, keeps web research off and selects the DS4
budgets. It allows five active jobs, five simultaneous model requests across
all jobs, and up to five independent workers within one Assistant turn.
Those workers share the global five-request capacity. A Proof run still
solves and reviews in sequence. Logs and the PID stay under
`.local/deepinfra/`. The browser receives
the local GUI session token, never the DeepInfra key. Existing workspaces and
servers are not used by this launcher. Use `--port N` if 8780 is occupied.
`--profile ds4` and `--service-tier standard` are the defaults. For Priority
service, which costs 50% more per token, launch with:

```bash
python3 scripts/start-deepinfra.py --profile ds4 --service-tier priority
```

The selection applies to every DeepInfra generation request after launch,
without changing Proof budgets or the credential's spending limit. Priority
does not guarantee a particular token rate. Pause active work and stop the old
GUI before relaunching on the same port; resume the saved jobs afterwards.
An explicit `--service-tier` is rejected with the Qwen profile. For the old
Qwen setup, select it explicitly:

```bash
python3 scripts/start-deepinfra.py --profile qwen
```

The Qwen GUI opens on port 8781 by default and uses the existing local
OpenAI-compatible server at
`http://127.0.0.1:18020`, model `qwen3.8-27b`, and its previous 65,536-token
context and Proof rules, with one active job, one model request and one
Assistant worker at a time. Start the model server and SSH tunnel first, as in
Plan B; selecting this profile does not allocate a GPU or create a tunnel.
Its isolated workspace, log and PID live under `.local/qwen-test/`; it does
not need or send a DeepInfra credential. Only one GUI can occupy a port.
Use `--host URL --model ID` if the Qwen server advertises a different endpoint
or model ID. `--ctx N` selects a different context supported by that server.

For a manual launch, set `DEEPINFRA_API_KEY_FILE` to the private credential
file, then use:

```bash
python -m mathagent --gui --backend openai --api-profile deepinfra \
  --allow-remote-model --offline --workspace WORKSPACE
```

Here `--api-profile deepinfra` selects the DS4 transport and budget profile.
For a manual launch, `SH_DEEPINFRA_SERVICE_TIER=priority` selects Priority;
unset it or use `standard` for Standard service. The isolated launcher sets
this value from its flag, so an inherited environment value cannot change its
default pricing.
A manual Qwen launch uses `--api-profile default` with the local server and
model settings from B4. Selecting a different profile never rewrites saved
jobs or grants them new allowances on resume.

New DS4 Proof jobs use 384,000 tokens for each solve, repair and review from
Medium upwards. Medium, High, Extra high and Poincaré allow 3, 5, 7 and 10 solver
passes; Low remains one short solve/review pass. The total output allowance
includes one possible review-format retry per pass. See the budget table in
[guide.md](guide.md). The common per-call time guard defaults to three hours;
use `--proof-call-seconds SECONDS` for an explicit value in either the DS4
launcher or a manual launch.
The workflow time allowance is derived from the permitted calls instead of the
old short effort-specific deadline. Explicit token and time flags remain hard
limits; they may stop a job before its maximum pass count. The scoped API key's
spending allowance is separate and remains unchanged by selecting an effort.

The profile pins `deepseek-ai/DeepSeek-V4.1-Flash-1017` at
`https://api.deepinfra.com/v1/openai`. It uses ordinary streamed Chat
Completions, with no stored Batch API or optional retained cache. The model
catalog is read without authentication because scoped inference JWTs are
rejected by the authenticated catalog route. Prompt preflight uses the native
tokenizer when available, with additional framing/tool/schema allowances;
otherwise it is explicitly labelled a UTF-8 byte estimate. Actual input and
generated-token usage comes from the completed inference response. No exact
reconstruction of the provider's chat template is claimed.

An older model-restricted credential may permit only V4 Flash 0731. A model
upgrade therefore needs a credential permitting V4.1 Flash 1017; do not
silently mint a fresh spending allowance. Existing 0731 jobs retain their
saved checkpoint and budgets, and require access to that checkpoint when
resumed. Changing the default model does not rewrite saved work.

If a response stalls, Pause closes the active response socket and preserves
the partial work. It does not wait for another generated chunk. Connection
setup and the wait for initial HTTP headers still obey their request timeout.
After recovering an interrupted turn, use Resume in the same conversation;
an incomplete fragment is never treated as a completed proof.

This profile makes a small structured routing call for an unassigned new
Assistant request. It uses at most 1,024 output tokens and 60 seconds, charged
to the existing allowance. Very elementary identities may stay direct;
nontrivial proof requests start Proof mode. Explicit `@` selections and the
existing automatic source/reference routes bypass this extra decision.

After Proof has been selected, Qwen and DS4 prepare a short delegation through
the existing Assistant tool loop. This stage has its own hard limit: at most
two calls of 2,048 generated tokens each, including any generated reasoning
and tool arguments. Preparation also allows at most 16,384 input tokens per
call and 32,768 in total, from the backend's count or a labelled estimate.
Larger requests hand off to Proof without truncating the original material.
The first call may read context; the next must delegate to Proof. The
model-written brief is at most 4,000 characters and collects definitions,
the precise target and ambiguities, with at most one short, explicitly
unproved suggested direction. It must not
attempt the proof.

If the brief is unavailable or the limit is reached, the controller starts
Proof from the original request and saved context without another preparation
attempt; unfinished preparation is not used as the task brief. Original
hypotheses and exact conversation stay authoritative for
solving and review. Preparation remains charged to the existing Assistant
budget; Proof's solve/review allowances do not change. The preparation limits
persist across pause/resume. An older paused turn already over them hands off
without receiving another preparation allowance. Resume leaves
already-created Proof jobs unchanged. Standalone Proof and raw benchmark
calls retain their direct path. The Proof page's Sources tab shows Original
request and, when the delegated objective differs, Prepared task.

## Plan A: local model

Ollama by default (A1–A3). If the user chose HyperQwen in step 1, go to
[A4](#a4-optional-hyperqwen-instead-of-ollama) instead.

### A1. Install and start Ollama

Check whether an Ollama server is running: `curl -s
http://127.0.0.1:11434/api/tags` prints JSON. If it does, go to A2.

- **Ollama is installed (`command -v ollama`) but not running:** start it with
  `nohup ollama serve > "$REPO/.local/ollama.log" 2>&1 &` (on macOS, open the
  Ollama app instead). Then repeat the check until it prints JSON. The first
  start can take about 10 seconds.
- **Linux, with the user's permission for `sudo`:**
  `curl -fsSL https://ollama.com/install.sh | sh`. It installs a service that
  starts automatically.
- **Linux without `sudo`** (user space, about 2 GB unpacked):

  ```bash
  ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
  mkdir -p ~/.local/ollama
  curl -fL "https://ollama.com/download/ollama-linux-$ARCH.tar.zst" | zstd -d | tar -x -C ~/.local/ollama
  ```

  Without `zstd`, unpack with Python instead:

  ```bash
  .venv/bin/python -m pip install zstandard
  .venv/bin/python -c "import tarfile,urllib.request,zstandard,os; r=urllib.request.urlopen('https://ollama.com/download/ollama-linux-$ARCH.tar.zst'); tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(r), mode='r|').extractall(os.path.expanduser('~/.local/ollama'))"
  ```

  For an AMD GPU, also unpack `ollama-linux-amd64-rocm.tar.zst` into the same
  folder. Start the server in the background:

  ```bash
  nohup ~/.local/ollama/bin/ollama serve > ~/.local/ollama/serve.log 2>&1 &
  ```

  Set `OLLAMA_MODELS=<folder>` before `serve` to keep models on a larger disk.
  Use `~/.local/ollama/bin/ollama` wherever this guide says `ollama`.
- **macOS:** install the app from https://ollama.com/download (it starts the
  server), or run `brew install ollama` and then `brew services start ollama`.

Check: `curl -s http://127.0.0.1:11434/api/tags` prints JSON.

Alternative on Apple Silicon: if the user already runs an OpenAI-compatible MLX
server, use it instead of Ollama with `--backend openai --api-profile mlx --host
http://127.0.0.1:<port> --model <served model id>`. Check its protocol first with
`.venv/bin/python scripts/test-local-mlx.py --host <url> --model <id>`. This
profile does not support seeds or a thinking budget.

### A2. Choose the model

Take the first row the machine satisfies. "GPU" means NVIDIA VRAM with a working
driver (`nvidia-smi` succeeds); on a Mac, count the unified memory.

| Machine | Model | Download | Settings |
| --- | --- | ---: | --- |
| GPU ≥ 32 GB, or Mac ≥ 48 GB | `qwen3.8:27b` | 18 GB | full |
| GPU 24 GB, or Mac 32 GB | `qwen3.8:27b` | 18 GB | medium |
| GPU 12–16 GB, or Mac 16–24 GB | `qwen3.5:9b` | 6.6–7.6 GB | small |
| GPU 8 GB | `qwen3.5:4b` | 3.3–4.0 GB | small |
| Anything else, or a software test | `qwen3.5:2b` (or `qwen3.5:0.8b`, faster) | 2.7–3.1 GB (1.0–1.3 GB) | tiny |

| Settings | Flags |
| --- | --- |
| full | none: the defaults (`--ctx 40960 --proof-solve-tokens 32768 --proof-verify-tokens 16384`) |
| medium | `--ctx 32768 --proof-solve-tokens 16384 --proof-verify-tokens 8192` |
| small | `--ctx 16384 --proof-solve-tokens 8192 --proof-verify-tokens 4096` |
| tiny | `--ctx 16384 --predict 2048 --proof-solve-tokens 4096 --proof-verify-tokens 2048` |

- `qwen3.8:27b` is the harness's default model and the one it is tuned for.
- The medium values match the team's 32k-context profile for a 24 GB GPU.
- Tiny was tested on a CPU-only laptop with `qwen3.5:2b`: a short chat answer
  took about 30 s and a short proof about 3.5 minutes.
- Small values are starting points. If Ollama runs out of memory, or its log
  shows layers moved to the CPU, use the next row's settings.

Don't go below `--ctx 16384` with Ollama. The harness cannot count tokens for
Ollama, so it bounds each request conservatively from its size in bytes. Chat
requests carry tool definitions, which makes the estimate about four times the
real prompt; at `--ctx 8192` the harness refuses them. It also enforces
`128 ≤ --predict < --ctx − 1024` (the default `--predict` is 4096). Automatic
numerical sanity checks need `--ctx` ≥ 32768, so the small and tiny settings
don't offer them.

Record the choice (this example is the tiny row), then pull the model unless
`ollama list` already shows it. Pulling an installed tag updates it to the
registry's current version, which can mean a new multi-GB download.

```bash
cd "$REPO" && cat >> .local/square.env <<'EOF'
MODEL=qwen3.5:2b
FLAGS="--model $MODEL --ctx 16384 --predict 2048 --proof-solve-tokens 4096 --proof-verify-tokens 2048"
EOF
. .local/square.env
ollama list | grep -q "^$MODEL " || ollama pull "$MODEL"
curl -s http://127.0.0.1:11434/api/tags | grep -o "\"name\":\"$MODEL\""    # must print the tag
```

### A3. Check the harness with the model

The harness's default backend is Ollama on `http://localhost:11434`, so `FLAGS`
holds only the model and its settings.

```bash
cd "$REPO" && . .local/square.env
timeout 600 .venv/bin/square-harness --workspace "$WS" $FLAGS \
  --mode explore --no-think --prompt "In one sentence: what is a prime number?"; echo "exit $?"
```

Expected: a one-sentence answer and exit status 0. Then run a short proof job,
the harness's main workflow:

```bash
cd "$REPO" && . .local/square.env
timeout 1200 .venv/bin/square-harness --workspace "$WS" $FLAGS \
  --proof-rounds 1 --proof-seconds 600 \
  --prompt "Prove that the sum of two even integers is even."; echo "exit $?"
.venv/bin/square-harness --workspace "$WS" $FLAGS --prompt "/proofs"
```

Expected:

- exit status 0 (completed) or 1 (ended without an approved proof);
- `/proofs` lists the job.

For the software check, either status passes: tiny models often fail the
mathematics. Exit status 130 means the job was interrupted; look at the
messages. When output is not a terminal, long paths in it are wrapped at 80
columns, so find files with `ls` rather than copying them from the output. Then
go to [step 3](#3-start-the-web-app).

### A4. Optional: HyperQwen instead of Ollama

[HyperQwen](https://github.com/syv-ai/HyperQwen) serves Qwen 3.8 27B with 4-bit
weights and speculative decoding on one NVIDIA GPU of 24 GB. The team runs it on
its cluster (Plan B); its authors test it on an RTX 3090. Requirements:

- Linux, an NVIDIA GPU with 24 GB or more, and a working driver (`nvidia-smi`);
- Docker with the Compose plugin and the NVIDIA Container Toolkit, usable
  without `sudo`;
- about 40 GB of free disk: a 9.5 GB image and the prepared model.

Check that Docker reaches the GPU, then start HyperQwen with its own Compose
file:

```bash
docker compose version && docker run --rm --gpus all ubuntu nvidia-smi    # must list the GPU
git clone https://github.com/syv-ai/HyperQwen.git ~/HyperQwen
cd ~/HyperQwen && nohup docker compose --profile single up -d > ~/HyperQwen/up.log 2>&1 &
```

- The first start downloads the image, prepares the model, then compiles
  kernels: allow 30–60 minutes. `up` waits for the preparation, so keep it in
  the background as above. Poll every few minutes with
  `curl -sf http://127.0.0.1:18020/health && echo ready`. Read progress in
  `~/HyperQwen/up.log` and with `docker compose --profile single logs --tail 20`
  in `~/HyperQwen`.
- The server listens on `127.0.0.1:18020` only. Don't set `VLLM_API_KEY` or
  `BIND` in its `.env`: the harness sends no API key.
- Compose uses HyperQwen's latest image; the cluster plan pins the version the
  team tested.
- It starts again with the computer and keeps the GPU memory until
  `docker compose --profile single stop` (in `~/HyperQwen`).
- Fall back to Ollama (A1–A3) if Docker cannot reach the GPU and the user
  declines to install the NVIDIA Container Toolkit (it needs `sudo`), or if the
  server fails, for example out of GPU memory because the desktop uses part of
  it.

When `ready` prints, continue with [B4](#b4-check-the-harness-with-the-model).
Skip B1–B3: there is no cluster and no tunnel.

## Plan B: model on a cluster GPU, reached by SSH

The cluster only serves the model. The harness, the web app and the user's files
stay on this computer:

```text
this computer                                        GPU node
square-harness --gui ── 127.0.0.1:18020 ══ssh══▶ 127.0.0.1:18020  model server (vLLM or Ollama)
```

Because the tunnel ends on `127.0.0.1`, the harness keeps its default offline
mode for the model.

### B1. Inspect the GPU machine

Check that `ssh <login>` works without a password prompt. If it asks for a
password, ask the user to set up an SSH key or to run the session themselves.
Then:

```bash
ssh <login> 'hostname; sinfo -s 2>/dev/null; sinfo -o "%P %G %l %D" 2>/dev/null; command -v apptainer singularity docker; echo proxy=$https_proxy; df -h ~ | tail -1; python3 --version'
```

- With Slurm, find a GPU partition and its GPU type (`%G`).
- Partitions that allow preemption (`scontrol show partition <p>` shows
  `PreemptMode=REQUEUE`) can stop your job at any time. Prefer a partition
  without preemption.
- Without Slurm (a GPU server you can SSH into directly), run the same commands
  there; `nvidia-smi` shows the GPU.
- Compute nodes may reach the internet only through a proxy (`https_proxy` on
  the login node). Pass it into any download job.
- `/home` is often small: keep images and weights (20–60 GB) on a scratch or
  project disk if `/home` has less than 60 GB free.

### B2. Choose and prepare the model server

| GPU memory | Server | Context (`MAX_LEN`) |
| --- | --- | ---: |
| 24 GB (A10, RTX 3090/4090, L4) | HyperQwen image, W4A16 weights | 40,960 |
| 40–48 GB (L40S, L40, A6000, A100-40) | HyperQwen image, W4A16 weights | 65,536 |
| ≥ 80 GB (A100-80, H100, H200) | HyperQwen as above, or vLLM with BF16 weights | 65,536 |
| No container runtime, or anything fails | Ollama in user space | see A2 |

**HyperQwen**, the team's tested setup, serves Qwen3.8-27B through vLLM, with 4-bit
weights and speculative decoding. Its image and settings are pinned in
[`deployment/hyperqwen-a10.lock.json`](../deployment/hyperqwen-a10.lock.json)
(the 24 GB profile). It needs Apptainer (or Singularity) on a Slurm cluster, or
Docker with the NVIDIA Container Toolkit on a plain GPU server.

One-time setup, on the cluster (CPU only, about 1 h, about 32 GB of disk). Save as
`hq-setup.sbatch`, fill in the partition, then run `sbatch hq-setup.sbatch`:

```bash
#!/usr/bin/env bash
#SBATCH --job-name=hq-setup
#SBATCH --partition=<partition>
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=%x-%j.log
set -euo pipefail
HQ_ROOT=${HQ_ROOT:-$HOME/hyperqwen}         # a disk with ~40 GB free
IMAGE=ghcr.io/syv-ai/hyperqwen@sha256:6ac4aea86882fae42381b701bb3eb20a1df94a93a62e23f0e1edb3c8b34d2819
mkdir -p "$HQ_ROOT/models" "$HQ_ROOT/cache"
export APPTAINER_TMPDIR=${TMPDIR:-/tmp}/apptainer-${SLURM_JOB_ID:-$$} APPTAINER_CACHEDIR=${TMPDIR:-/tmp}/apptainer-cache-$USER
mkdir -p "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"; trap 'rm -rf "$APPTAINER_TMPDIR"' EXIT
[ -s "$HQ_ROOT/hyperqwen.sif" ] || { apptainer pull "$HQ_ROOT/hyperqwen.sif.partial" "docker://$IMAGE"; mv "$HQ_ROOT/hyperqwen.sif.partial" "$HQ_ROOT/hyperqwen.sif"; }
ENV_FILE=$APPTAINER_TMPDIR/env        # an env file: --env splits values on commas
printf '%s\n' HF_XET_HIGH_PERFORMANCE=1 "https_proxy=${https_proxy:-}" "http_proxy=${http_proxy:-}" \
  "HTTPS_PROXY=${https_proxy:-}" "HTTP_PROXY=${http_proxy:-}" "no_proxy=${no_proxy:-}" > "$ENV_FILE"
apptainer exec --cleanenv --env-file "$ENV_FILE" -B "$HQ_ROOT/models:/app/models" --home "$HQ_ROOT/cache:/cache" \
  "$HQ_ROOT/hyperqwen.sif" bash /app/docker/entrypoint.sh prepare     # downloads and requantizes ~22 GB, resumable
du -sh "$HQ_ROOT"/models/*
```

Each session, start the model server on a GPU node (`hq-serve.sbatch`). Fill in
the partition, the GPU request and the three values from the table below:

```bash
#!/usr/bin/env bash
#SBATCH --job-name=hq-serve
#SBATCH --partition=<gpu-partition>
#SBATCH --gres=gpu:1                   # or gpu:<type>:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --output=%x-%j.log
set -euo pipefail
HQ_ROOT=${HQ_ROOT:-$HOME/hyperqwen}
PORT=18020
ENV_FILE=$(mktemp "${TMPDIR:-/tmp}/hq-env.XXXXXX")
cat > "$ENV_FILE" <<EOF
HOST=127.0.0.1
PORT=$PORT
VLLM_NO_USAGE_STATS=1
DO_NOT_TRACK=1
no_proxy=localhost,127.0.0.1
MODEL=/app/models/Qwen3.8-27B-W4A16-AutoRound-fast
MAX_LEN=65536
KV_MEM=12884901888
MAX_SEQS=4
PREFIX_CACHE=0
SPEC=dflash2
DFLASH_TOKENS=7
INT8_ACT=
PREFILL_ATTN=
EXTRA_ARGS='--generation-config vllm --no-enable-prefix-caching'
EOF
echo "== node $(hostname -s), job ${SLURM_JOB_ID:-none}"; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
apptainer exec --nv --cleanenv --env-file "$ENV_FILE" -B "$HQ_ROOT/models:/app/models" --home "$HQ_ROOT/cache:/cache" \
  "$HQ_ROOT/hyperqwen.sif" bash /app/docker/entrypoint.sh single &
SERVER=$!; trap 'kill $SERVER 2>/dev/null; wait $SERVER 2>/dev/null' EXIT
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; do kill -0 $SERVER 2>/dev/null || exit 1; sleep 10; done
echo "== model server ready on $(hostname -s) 127.0.0.1:$PORT"; curl -s "http://127.0.0.1:$PORT/v1/models"; echo
wait $SERVER
```

| GPU memory | `MAX_LEN` | `KV_MEM` | `MAX_SEQS` |
| --- | ---: | ---: | ---: |
| 24 GB | 40960 | 4294967296 | 2 |
| 40–48 GB and more | 65536 | 12884901888 | 4 |

Submit with `sbatch hq-serve.sbatch`. Find the node with `squeue -u $USER`.
Wait for `model server ready` in `hq-serve-<jobid>.log`. The first start compiles
kernels into `$HQ_ROOT/cache` and takes 15–30 minutes; later starts take a few
minutes.

Without Slurm (a GPU server you reach directly), run both scripts with `bash`
instead of `sbatch`, the server in the background:
`nohup bash hq-serve.sbatch > hq-serve.log 2>&1 &`. On a GPU server with Docker
instead of Apptainer, start HyperQwen there with its own Compose setup, as in
[A4](#a4-optional-hyperqwen-instead-of-ollama); it also listens on
`127.0.0.1:18020` only.

**vLLM with BF16 weights** (≥ 80 GB, not yet tested on a cluster). Use the
settings in [`scripts/serve-vllm.sh`](../scripts/serve-vllm.sh) (image, pinned
model revision, `--reasoning-parser qwen3`, `--tool-call-parser qwen3_xml`), with
`--host 127.0.0.1 --port 18020 --max-model-len 65536`. On Slurm, run the image
with `apptainer exec --nv … vllm serve <model> <options>`. The served model ID is
`square-qwen`; use it as `--model`.

**Ollama fallback** (any GPU, no container needed). Install Ollama in user space
on the cluster as in [A1](#a1-install-and-start-ollama), with `OLLAMA_MODELS` on
a large disk. In the GPU job, run
`OLLAMA_HOST=127.0.0.1:18020 ~/.local/ollama/bin/ollama serve`. Pull the model
once from a node that has internet access: run `ollama serve` there on another
port, then `OLLAMA_HOST=127.0.0.1:<port> ollama pull qwen3.8:27b`.

### B3. Open the tunnel

```bash
NODE=<node from squeue>      # or the GPU server itself
ssh -f -N -o ServerAliveInterval=60 -o ExitOnForwardFailure=yes \
  -J <user>@<login-host> -L 18020:127.0.0.1:18020 <user>@$NODE     # drop -J for a direct server
curl -s http://127.0.0.1:18020/v1/models       # vLLM/HyperQwen: lists qwen3.8-27b (or square-qwen)
curl -s http://127.0.0.1:18020/api/tags        # Ollama: lists qwen3.8:27b
```

- Many clusters (`pam_slurm_adopt`) accept SSH into a compute node only while you
  have a job running there.
- `Host key verification failed` on a new node: compare
  `ssh <login> 'ssh-keyscan -t ed25519 <node>' | ssh-keygen -lf -` with the
  fingerprint the node presents (`ssh -v -o BatchMode=yes -J <login> <node> true`,
  line "Server host key"). If they match, add the keyscan line to
  `~/.ssh/known_hosts` (hashed: `ssh-keygen -H`). If they differ, stop and tell
  the user.
- If compute nodes refuse SSH entirely, do not expose the model port on the
  cluster network. The harness sends no API key, so anyone on that network could
  use the model. Explain this to the user and ask how to proceed.
- To make reconnecting easy, offer to add to `~/.ssh/config`:
  `Host <node-pattern>` with `ProxyJump <user>@<login-host>`.

### B4. Check the harness with the model

Read the served model's `id` and `max_model_len` (its context length):

```bash
curl -s http://127.0.0.1:18020/v1/models
```

For HyperQwen, set the thinking budget the server supports. It reserves at most
75% of each answer's output allowance for reasoning. Record the settings, with
`MODEL` set to the `id` and `CTX` to the `max_model_len` printed above (the
cluster profiles serve `qwen3.8-27b` with 40960 or 65536):

```bash
cd "$REPO" && cat >> .local/square.env <<'EOF'
export SQUARE_OPENAI_THINKING_FRACTION=0.75 SQUARE_OPENAI_STRUCTURED_THINKING_FRACTION=0.75
MODEL=qwen3.8-27b
CTX=65536
FLAGS="--backend openai --host http://127.0.0.1:18020 --model $MODEL --ctx $CTX --proof-solve-tokens 32768 --proof-verify-tokens 16384 --request-timeout 1800"
EOF
. .local/square.env
timeout 600 .venv/bin/square-harness --workspace "$WS" $FLAGS --mode explore --no-think \
  --prompt "In one sentence: what is a prime number?"; echo "exit $?"
```

- For stock vLLM, the `id` is `square-qwen`. Keep the `export` line only if
  that build accepts `thinking_token_budget`.
- For Ollama through the tunnel, use `FLAGS="--backend ollama --host
  http://127.0.0.1:18020 --model qwen3.8:27b"` plus the A2 settings for the GPU's
  memory, and no `export` line.

Expected: a one-sentence answer and exit status 0. Then run the proof check from
A3; on these GPUs it takes a few minutes at most.

## 3. Start the web app

Launch it in the background, with the `FLAGS` that passed the checks:

```bash
cd "$REPO" && . .local/square.env
nohup "$REPO/.venv/bin/square-harness" --gui --no-browser --gui-port 8765 --workspace "$WS" $FLAGS \
  > "$REPO/.local/gui.log" 2>&1 < /dev/null &
sleep 5; grep -o 'Open: http://[^/]*' "$REPO/.local/gui.log"
```

- Port 8765 taken: the next free port is used. The `grep` above prints the
  address of the `Open:` line without its token.
- Launch with the absolute path, as above, so that step 5 can find the process.
- To let the user switch between project folders, add `--gui-root <parent>`,
  where `<parent>` contains `$WS`. The token file then lives in
  `<parent>/.mathagent/`.
- Keep `--gui-host` at its default (`127.0.0.1`). For a phone, see "On a phone"
  in [guide.md](guide.md#the-web-app), and explain the plain-HTTP risk to the user
  first.

Check that the server answers and accepts the token, without printing the token:

```bash
cd "$REPO" && . .local/square.env
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(cat "$WS/.mathagent/gui-token")" \
  http://127.0.0.1:<port>/api/status        # 200; 401 means the token was not accepted
```

On a desktop, open the `Open:` address for the user (`xdg-open` on Linux, `open`
on macOS).

## 4. Optional parts

Install the parts the user chose in step 1:

- **Python sandbox** (`@python_lab`, `@sanity_check`): requires Docker (Docker
  Engine on Linux, Docker Desktop on macOS). From the repository, run
  `.venv/bin/python -m mathagent.numerics build`, then
  `.venv/bin/python -m mathagent.numerics doctor`; the doctor must report the
  image and a successful test run. Without Docker, the harness works and these
  two skills explain what is missing. Snap-packaged Docker makes `doctor` report
  `"no_new_privileges": "process"`; that is expected.
- **Write-up compilation**: `latexmk` with a TeX distribution (TeX Live, or MacTeX
  on macOS); bibliographies also use BibTeX or Biber, which both include. Without
  `latexmk`, write-ups produce `.tex` files that are not compiled.
- **Search keys** (optional, set in the environment of the web app):
  `SEMANTIC_SCHOLAR_API_KEY` (fewer rate limits) and `BRAVE_SEARCH_API_KEY`
  (general web search for the Assistant). Literature tools work without keys.
- Never add `--allow-python` (unsandboxed Python running as the user) unless the
  user explicitly asks for it.

## 5. Report to the user

Tell the user:

1. the `Open:` address (in `$REPO/.local/gui.log`), and that it contains a
   secret token. The token stays the same across restarts; deleting
   `<gui-root>/.mathagent/gui-token` revokes it;
2. that the web app searches scholarly sources online by default (short public
   queries, never their files), and that launching with `--offline` turns this
   off;
3. how to stop it gracefully:
   - find the PID with `pgrep -af "$REPO/.venv/bin/square-harness --gui"` and
     check that the command line is the one you started;
   - stop it with `kill -INT <PID>`, which pauses running work at its
     checkpoint (a plain `kill` stops without pausing);
4. the command that starts it again: the `nohup` line from step 3, after
   `cd <repo> && . .local/square.env`;
5. **Plan A**: the model, its settings, and whether Ollama starts by itself
   (service or app) or must be started with `ollama serve`. With HyperQwen (A4):
   it starts with the computer and holds the GPU memory until
   `docker compose --profile single stop` in `~/HyperQwen`;
6. **Plan B**:
   - the job ID, the node, and when the job ends (`squeue -j <id> -o "%L %e"`);
   - how to restart: submit `hq-serve.sbatch`, reopen the tunnel, restart the
     web app with the same command;
   - `scancel <id>` frees the GPU.

   When the job ends, the model disappears. A new job may land on another node:
   then only the tunnel changes;
7. which optional parts are installed, and anything that failed.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Offline mode requires a localhost/loopback model --host` | The model must be reached through `127.0.0.1` (a tunnel). Use `--online` only with the user's consent to send data to that host. |
| `The complete request needs N input tokens … leaves 0 output tokens` | Raise `--ctx` (16384 at least with Ollama, whose token count is a conservative byte estimate). |
| `Use ctx >= 2048, 128 <= predict < ctx - 1024 …` | Lower `--predict`, or raise `--ctx`. |
| `--gui-root must be a folder that contains the --workspace folder` / no such directory | `mkdir -p` the workspace; make `--gui-root` a parent of it. |
| Connection refused on 11434 or 18020 | Server not running, or tunnel closed. Check the server log, `ss -ltnp`, and `pgrep -af "ssh.*18020"`. |
| Model not found | Pull it (`ollama pull <tag>`), or use the exact ID from `/api/tags` (Ollama) or `/v1/models` (vLLM). |
| `budget_violation` or `completion_tokens above the requested output cap` | You used `--backend openai` against Ollama's `/v1` endpoint, which ignores output caps. Use `--backend ollama`. |
| Empty answers in chat with a small model | It spent the output allowance thinking. Add `--no-think` for terminal chat, or raise `--predict`. |
| A Low-effort job ends almost at once on CPU | Low allows one minute. Choose Medium or more in the web app. |
| Ollama out of memory, or very slow | Use the settings of a smaller row in A2, or a smaller model. |
| `ModuleNotFoundError: mathagent` | Run `.venv/bin/python -m pip install -e .` again, or use the `PYTHONPATH` form from step 2. |
| Python too old on a login node | Use uv (step 2 fallback). |
| Slurm job pending for long | `squeue -j <id> -o "%R"` shows why. Choose another partition or GPU type, or a shorter `--time`. |
| Slurm job requeued or killed early | The partition preempts. Use one without `PreemptMode=REQUEUE`. |
| SSH to the node refused | You have no running job on that node (`pam_slurm_adopt`), or the node name is wrong. |
| First HyperQwen start seems stuck | Model preparation and kernel compilation: 15–30 min on a cluster GPU, up to an hour on a first local start. Follow the job log, or `~/HyperQwen/up.log` and `docker compose --profile single logs --tail 20` (A4). |
| Docker: `could not select device driver "" with capabilities: [[gpu]]` | The NVIDIA Container Toolkit is missing or not set up for Docker. Installing it needs `sudo`: ask the user, or use Ollama (A1–A3). |
| HyperQwen container exits or stays unhealthy (A4) | Read `docker compose --profile single logs --tail 100` in `~/HyperQwen`. Out of GPU memory: close programs that use the GPU (`nvidia-smi` lists them), or use Ollama. |
| Docker `permission denied` for the sandbox | The user is not in the `docker` group. Ask the user (it needs `sudo`), or skip the sandbox. |
