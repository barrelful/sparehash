# Owner-supervised physical CPU contention measurement

From the reviewed repository checkout, as your ordinary user in an interactive
SSH terminal (PTY), run **without sudo**:

```bash
python3 qa/run_cpu_contention.py
```

Press Enter for a new session. No installation, dependencies, filenames or raw
JSON interpretation are required. The assistant must **not** execute this command
on the physical workstation. This developer tool is not a SpareHash runtime feature.

## Before starting

Save work, close games and unnecessary applications, and keep the desktop,
power/cooling settings, SSH launch method and background applications comparable
between rounds. Keep existing protections enabled. Have a second terminal ready
for your manual service actions. The runner never controls services, upgrades
anything, changes governors, modifies kernel/security settings or kills unrelated
processes. Do not paste wallets, pool credentials or private logs into it.

The default is 24 workers when at least 24 logical CPUs are affinity-available,
otherwise the available count. It prints CPU model, physical cores, affinity
count, parameters, sensor, temperature, safe service states and installed-source
evidence before your confirmation. Use a smaller identical count in both rounds
if desired:

```bash
python3 qa/run_cpu_contention.py --workers 12
```

Defaults per condition: one 1-second warm-up (excluded from throughput medians),
then three 4-second SHA-256 samples. The runner reuses `qa/cpu_contention.py`'s
fixed workload and percentage comparison. Each phase owns fresh spawned workers;
each sample synchronises them through a ready barrier. Aggregate throughput is
total input MiB / parent-observed elapsed time, including dispatch/completion
overhead; worker elapsed/CPU times and scheduling are retained separately.

## Guided sequence — one command per round

1. Confirm both controller and miner are stopped by typing `STOPPED`.
   The runner verifies inactive/dead and PID 0 before baseline A.
2. After baseline A, **you** manually start mining in your other terminal:
   `sudo systemctl start sparehash.service`. Press Enter for the activity check.
3. Both services must be active/running and the miner SCHED_IDLE/nice 19.
   Use your existing private monitor to confirm actual hashing has stabilised,
   then type `HASHING`. The runner cannot prove hashing from service activity.
4. The identical mining-condition workload runs. If ordinary thermal/gaming
   protections pause mining, the runner aborts instead of bypassing them.
5. **You** manually stop the controller in your other terminal:
   `sudo systemctl stop sparehash.service`. Verify both services with
   `systemctl is-active sparehash.service` and
   `systemctl is-active sparehash-xmrig.service`, then type `STOPPED`.
6. The runner verifies both inactive/dead, measures baseline B, and prints both
   degradation percentages, stopped-baseline drift, thermal data and scheduling.
   It leaves services stopped and never restarts them.

Each prompt has a five-minute deadline with thermal monitoring still running.
Unexpected input cancels. Do not update the installation within a session.

**Round 1:** run against the existing physical `f348ed1` installation. A newer
repository checkout containing this tool does **not** update that installation.
**Round 2:** only after your separate manual update to a later reviewed commit,
run the same command from the **same tool checkout**, with identical parameters
and foreground launch context. Updating SpareHash is not part of the runner.

After round 2, type `COMPARE` at the final prompt, then select the two completed
sessions from the numbered list. You need no filenames or additional comparison
commands. To revisit later, launch the same command and type `COMPARE` at its
opening prompt (or use the optional `--compare` shortcut); neither comparison
path runs a workload or probes host services.

## Safety and abort behavior

- Foreground runner/workers require SCHED_OTHER/nice 0 and stable CPU affinity.
  Idle-scheduled foreground ancestry is refused. Service state, PIDs, invocation
  identity and readable effective miner controls are checked throughout work.
- Credible CPU hwmon drivers: `k10temp`, `zenpower`, `coretemp`. All discovered
  inputs on those drivers are monitored every **0.2 seconds**, including owner
  prompts. Any input at **80°C** aborts by default, below the installation's 85°C
  trip. Missing/malformed readings or evidence older than **1 second** abort.
  `--abort-c` permits only 40–80°C, never a higher limit.
- Service checks occur at least every 0.5 seconds between bounded queries
  (1-second query timeout). Temperature monitoring is independent. A missing
  mandatory service observation aborts; unreadable optional cgroup files are
  explicitly unavailable, not interpreted as scheduling support.
- **Ctrl+C** or any detected fault terminates/joins only this runner's explicitly
  owned worker handles, escalating to kill only those handles if required.
  No PID searches, process-group kills, `pkill`, or service actions are used.
- On failure/interruption, the runner saves an aborted report when storage is
  available and tells you to verify/manual-stop the controller. **It does not
  stop SpareHash for you.** Manually verify both services inactive afterward.
- This is sampled software monitoring, **not hard real-time thermal protection**:
  scheduler stalls, sensor lag, uninterruptible I/O or abrupt host failure can
  delay aborts, and samples are not guaranteed physical temperature peaks.
  Keep hardware and SpareHash protections enabled and supervise the machine.

## Reports, identity and interpretation

Results are automatically saved under
`~/sparehash-contention-results/session-TIMESTAMP-UNIQUE/` as `session.json` and
`report.txt`; comparisons have their own unique directory. `--output-dir` can
select another private user-owned, non-group/world-writable directory. Never
commit result folders. Aborted sessions are excluded from comparisons.

JSON records sample/worker throughput, wall/CPU times, affinity, PID/cgroup,
policy/nice, time-series CPU temperatures, current/maximum observed temperatures,
whitelisted service observations, `cpu.idle`, `cpu.weight`, foreground ancestry,
CPU/kernel/Python/OpenSSL identity and tool fingerprint. No configuration,
manifest, wallet, pool API, environment, command line or journal is read.

Installed identification hashes only the known public Python source/unit-template
inventory under `/opt/sparehash/src`, checking root-owned non-writable metadata
and ancestors. It compares public source against local Git archives for
`f348ed1` and HEAD. A match means **inventory file equality**, not installation
commit attestation, loaded-process identity or verification of XMRig's binary.
Other commits, unavailable source/history or alternate layouts may be unknown.
The checkout HEAD is never substituted for installed identity. Fingerprints and
observed scheduling remain useful even when no source commit match is available.

Degradation is `100 * (baseline - mining) / baseline`, reported against **both**
stopped medians. Negative values mean mining measured faster, often noise.
Baseline drift above 5% and within-phase sample spread above 10% produce warnings,
not cherry-picked baselines. Comparisons reject mismatched tool/parameters,
CPU/affinity, Python/OpenSSL, sensors, foreground hierarchy or ancestor controls.
SSH session numbers alone may differ. Kernel changes and between-round stopped
throughput drift are flagged; thermal and scheduling differences are displayed.
Repeat comparable rounds if drift or thermal differences confound interpretation.

**MEASUREMENT ONLY — no performance acceptance pass.** These measurements describe
one SHA-256 foreground workload, not game frame times, RandomX throughput,
universal desktop responsiveness, causal version attribution or release readiness.
Automated tests use mocks/fixtures and never execute CPU work on the physical host.
The older low-level `qa/cpu_contention.py` CLI remains for existing VM tooling;
it does not provide this supervised runner's continuous safety guarantees.
