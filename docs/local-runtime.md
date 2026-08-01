# Local dual-model runtime

The M4 workflow keeps its heavyweight model and generated state under the
repository's gitignored `.oddsfox-runtime/` tree. Because this checkout lives on
`/Volumes/Mac SSD`, model weights, Hugging Face and Torch caches, temporary files,
the Python virtual environment and pip cache, inference cache, logs,
qualification results, and discovery outputs stay on that SSD.

Use the operational wrapper from the repository root:

```bash
scripts/local_stack.sh paths
scripts/local_stack.sh setup
scripts/local_stack.sh start
scripts/local_stack.sh manifests
scripts/local_stack.sh check
scripts/local_stack.sh qualify
scripts/local_stack.sh smoke
scripts/local_stack.sh discover
scripts/local_stack.sh web-check
scripts/local_stack.sh serve
```

`setup` creates `.oddsfox-runtime/venv`, installs the Python project there, and
downloads the immutable Qwen3-4B Q8, Granite 3.3 2B Q8, MiniLM, and ModernBERT
revisions. The two llama.cpp servers listen only on loopback ports
8080 and 8081, accept browser origins only from localhost, and do not expose the
llama.cpp Web UI. `manifests` hashes the exact model files and binds the observed
runtime version and context. `check` validates both structured-output contracts
before qualification. PID files are treated as managed only when the recorded
process command matches the expected llama.cpp port and model alias, preventing
a stale PID from targeting an unrelated process.

`web-check` builds and tests the explorer while binding both the npm cache and
Playwright browser binaries to `.oddsfox-runtime/`, avoiding their default
locations on the internal system disk.

Each server reserves a 16,384-token context split across two parallel slots, so
every bounded request retains an 8,192-token effective context.

Qualification must return `AUTOMATION_VALIDATED` before discovery can publish.
The 5,000-proposition smoke run proves the end-to-end path before the complete
189,570-eligible-proposition run. The canonical file contains 94,781 market rows;
discovery reports and excludes its four invalid rows. All inference results are
committed incrementally to the SQLite cache, so an interrupted run can be
resumed with the same command. The wrapper runs qualification and discovery
under `caffeinate`, so macOS will not suspend an overnight foreground run.

The completed visualization is available while `serve` is running at
<http://127.0.0.1:8765>. This link is local to the machine; the service rejects
non-loopback hosts.

Set `ODDSFOX_RUNTIME_ROOT` only to move the runtime tree to another directory on
`/Volumes/Mac SSD`. The wrapper rejects other volumes unless
`ODDSFOX_ALLOW_NON_SSD_RUNTIME=1` is explicitly set.
