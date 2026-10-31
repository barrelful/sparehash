# Owner-supervised CPU contention comparison

`qa/cpu_contention.py` is a standard-library-only **foreground** workload, not
mining and not a SpareHash runtime feature. It never starts, stops, pauses or
configures SpareHash. The assistant must not execute it on the physical host.

## Safety and method

- Run as your ordinary desktop user, **without sudo**, at SCHED_OTHER/nice 0.
- Save work, close games and unnecessary applications; keep the same desktop,
  worker count and parameters throughout. No governor, BIOS, kernel or security
  changes are needed. Keep all existing battery, gaming and thermal protections.
- Each invocation runs a one-second warm-up and three four-second samples.
  Workers default to the available logical CPUs, capped at 32; specify the same
  explicit count for every run. SHA-256 input throughput is measured in MiB/s;
  this is not a game benchmark or a RandomX benchmark.
- Before/after each sample, readable CPU hwmon temperatures are saved. These
  snapshots are **not continuous peak-temperature monitoring**. Keep an existing
  temperature monitor visible. Stop if temperatures approach your 85°C mining
  limit, responsiveness worsens, or anything unexpected occurs.
- **Ctrl+C stops the comparison.** Workers finish their bounded current sample
  (normally at most four seconds). If necessary, use another terminal to stop
  only this script's worker processes, not unrelated Python applications. Stop
  mining with `sudo systemctl stop sparehash.service`; verify both services
  inactive. Never use a broad `pkill python`.
- Failed/interrupted output files are not valid measurements; choose a fresh
  filename for each retry. The script refuses to overwrite evidence.

## Exact owner-run procedure

Use the reviewed PR checkout containing this script, then:

```bash
cd /home/doublebarrel/sparehash-v020-test
git fetch origin review/v0.2-pre-rc
git checkout review/v0.2-pre-rc
git pull --ff-only origin review/v0.2-pre-rc
git rev-parse HEAD
mkdir -p "$HOME/sparehash-contention-results"

# Baseline: owner stops mining and verifies BOTH services inactive.
sudo systemctl stop sparehash.service
systemctl is-active sparehash.service
systemctl is-active sparehash-xmrig.service

python3 qa/cpu_contention.py --label stopped --workers 24 \
  --output "$HOME/sparehash-contention-results/stopped-1.json"
```

For the mining comparison, **only the supervising owner** may elect to start
mining. This is a separate manual decision, not an instruction for the assistant
to restart it after VM testing. Start only when the machine is safe and no game
or other safety blocker is active. No forced start or configuration edit:

```bash
sudo systemctl start sparehash.service
systemctl is-active sparehash.service
systemctl is-active sparehash-xmrig.service
# Wait for ordinary hashing/temperatures to settle, using your existing monitor.
python3 qa/cpu_contention.py --label mining --workers 24 \
  --output "$HOME/sparehash-contention-results/mining-1.json"

sudo systemctl stop sparehash.service
systemctl is-active sparehash.service
systemctl is-active sparehash-xmrig.service
python3 qa/cpu_contention.py --label stopped --workers 24 \
  --output "$HOME/sparehash-contention-results/stopped-2.json"

python3 qa/cpu_contention.py --compare \
  "$HOME/sparehash-contention-results/stopped-1.json" \
  "$HOME/sparehash-contention-results/mining-1.json"
python3 qa/cpu_contention.py --compare \
  "$HOME/sparehash-contention-results/stopped-2.json" \
  "$HOME/sparehash-contention-results/mining-1.json"
```

Use 24 only when all 24 logical CPUs are available; otherwise select the same
available count for every invocation. Repeat the stopped/mining/stopped sequence
with fresh filenames if practical. Compare both baseline medians to expose drift;
do not cherry-pick a favourable pair. Leave mining stopped afterward.

Output includes every sample, worker CPU time, policy/nice, temperatures and safe
service-state/PID scheduling evidence before/after the whole invocation. Policy
numbers are Linux `SCHED_OTHER=0`, `SCHED_IDLE=5`. It reads no configuration,
wallet, pool, process command lines or journals. Labels are owner assertions:
discard a run if the recorded miner state contradicts its label or hashing
stopped during it. For a paused baseline, use your normal `sudo sparehash pause`
control, verify the miner inactive, and label it `paused` instead.

The comparison reports absolute median throughput and
`100 * (baseline - mining) / baseline` degradation. Negative degradation means
the mining sample was faster, usually noise—not evidence that mining helps.
There is deliberately no universal PASS threshold: these measurements quantify
this workload, not gameplay frame times or physical thermal certification.
