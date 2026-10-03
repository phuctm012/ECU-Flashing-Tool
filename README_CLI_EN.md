# SFlash CLI — `SFlash_CLI.exe` user guide

Command-line tool for flashing ECU firmware over CAN (UDS / ISO 14229), with no GUI. This guide is for people running the prebuilt `.exe` — **no Python and no source code required**.

> Vietnamese version: `README_CLI.md`.

> If you already have Python and the project source, use `python cli.py ...` in place of `SFlash_CLI.exe ...`; every command below is otherwise identical. See the project's `README.md`.

---

## 1. Getting set up

Unzip or copy the **whole `SFlash_CLI\` folder** — not just the `.exe`, which needs the support files sitting next to it.

Check it runs:

```powershell
cd C:\tools\SFlash_CLI
.\SFlash_CLI.exe --version
```

`SFlash 3.1` means installation is done.

### What real hardware additionally needs

| Requirement | When | Notes |
|---|---|---|
| **Vector XL Driver Library** | Using `--hardware vector` | From Vector Informatik. Windows only. |
| **Security Access DLL** | When the ECU requires the OEM's own seed/key algorithm | An **external** file picked at run time — **not** bundled into the `.exe`. Must be 64-bit, matching `SFlash_CLI.exe`. |

Without either, everything still runs against the **Virtual ECU Simulator** (`--hardware virtual`, the default), which is the way to learn the tool or test a script.

---

## 2. The standard sequence — follow this order

The first three steps are **completely safe**: nothing is written to the ECU.

```powershell
# 1. Virtual ECU - a dry run, no hardware needed
.\SFlash_CLI.exe flash firmware.s19 --dry-run

# 2. Does this machine see the Vector hardware?
.\SFlash_CLI.exe list-hardware

# 3. Is the Security DLL usable? (never touches the ECU)
.\SFlash_CLI.exe check-security-dll C:\tools\SeedKey64.dll

# 4. Does the ECU answer? Read-only - no Erase/Download/writes
.\SFlash_CLI.exe test-connection --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --timeout 120

# 5. Only once step 4 says PASSED, flash for real
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --security-dll C:\tools\SeedKey64.dll ^
    --timeout 900 --report report.html --json-summary result.json
```

> In PowerShell/CMD, a trailing `^` means "this command continues on the next line". Drop every `^` to write it on one line.

---

## 3. The commands

### `list-hardware` — what this machine can see

```powershell
.\SFlash_CLI.exe list-hardware
```

Lists the Vector channels currently plugged in, each with **the exact arguments to use** (e.g. `--channel 1 --serial 123456`), plus the CAN ID table per Radar Side.

If it reports no hardware: nothing is plugged in, or the Vector XL Driver is not installed.

### `info` — inspect a firmware file

```powershell
.\SFlash_CLI.exe info firmware.s19
.\SFlash_CLI.exe info app.s19 calib.s19        # several files
.\SFlash_CLI.exe info image.bin --base-address 0x8000
```

Prints segment count, addresses, size and checksum. Never touches an ECU.

### `check-security-dll` — diagnose a Security DLL

```powershell
.\SFlash_CLI.exe check-security-dll C:\tools\SeedKey64.dll
```

Reports whether the DLL is 32- or 64-bit, what SFlash itself is, which functions the DLL exports, which DLLs it depends on, and then actually tries to load it. Exit `0` means it is usable.

**Run this before the first flash with any new DLL.** It settles "will this DLL work" in one second, instead of finding out halfway through a flash.

### `test-connection` — a safe connectivity check

```powershell
.\SFlash_CLI.exe test-connection --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --timeout 120 --verbose
```

Opens an Extended Session and reads a few identification DIDs. It **never** touches the Programming Session, Security Access, Erase Memory, or any write command. It always tries to restore the ECU to the Default Session on the way out, including when something failed mid-probe.

Run it before every real flash on a bench you have not used yet.

### `flash` — flash firmware

```powershell
# One file
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --serial 123456 ^
    --sequence suzuki --security-dll C:\tools\SeedKey64.dll --timeout 900

# Several files (several datablocks, flashed in the order given)
.\SFlash_CLI.exe flash app.s19 calib.s19 --hardware vector --channel 0 --sequence suzuki

# Preview the steps without sending anything to the ECU
.\SFlash_CLI.exe flash firmware.s19 --sequence suzuki --dry-run
```

### `batch` — flash several ECUs one after another

Each ECU is identified first (so its serial number lands in the report), then flashed. One failing ECU does **not** stop the series.

```powershell
# 5 ECUs with the same settings, waiting for Enter between units
.\SFlash_CLI.exe batch firmware.s19 --hardware vector --channel 0 ^
    --count 5 --pause --report batch.html

# One configuration per ECU
.\SFlash_CLI.exe batch firmware.s19 --hardware vector ^
    --unit "name=Left,channel=0,side=s0" ^
    --unit "name=Right,channel=1,side=s1"
```

Its own options: `--count N`, `--pause` (wait for Enter between units — **off by default**, so a scripted run never blocks on input), `--no-identify`, `--stop-on-fail`.

### `parallel` — flash several ECUs at the same time

One CAN channel per ECU, all running concurrently.

```powershell
.\SFlash_CLI.exe parallel firmware.s19 --hardware vector ^
    --unit "name=Left,channel=0,serial=123456,side=s0" ^
    --unit "name=Right,channel=1,serial=123456,side=s1" ^
    --timeout 900 --report parallel.html
```

Two units pointing at the **same ECU** (same channel and same Tx ID) are rejected with exit `2` — two concurrent UDS sessions on one ECU corrupt each other and the result looks exactly like a hardware fault.

**Ctrl+C** is Abort All: every running channel is asked to stop before the tool exits.

### Describing several ECUs in a file (`--units-file`)

Shared by `batch` and `parallel`. Convenient for a fixed line setup.

```json
{
  "comment": "Line 1 - two radars per vehicle",
  "units": [
    { "name": "Left",  "hardware": "vector", "channel": 0, "serial": 123456, "side": "s0" },
    { "name": "Right", "hardware": "vector", "channel": 1, "serial": 123456, "side": "s1" }
  ]
}
```

```powershell
.\SFlash_CLI.exe parallel firmware.s19 --units-file line1.json --timeout 900
```

Fields: `name`, `hardware` (`virtual`/`vector`), `channel`, `serial`, `side` (`s0`/`s1`), `tx_id`, `rx_id`, `functional_id`. **Anything you leave out falls back to the command's own flags.** A misspelled field name is reported as an error, never silently ignored.

### `gitlab` — fetch firmware from GitLab

Needs an **access token**. Prefer the environment variable, which keeps the token out of your command history:

```powershell
$env:SFLASH_GITLAB_TOKEN = "glpat-xxxxxxxx"

.\SFlash_CLI.exe gitlab refs
.\SFlash_CLI.exe gitlab jobs --ref main --success-only
.\SFlash_CLI.exe gitlab artifact --ref main --job build_fw -o downloads --extract
.\SFlash_CLI.exe gitlab packages --package-name radar_fw
.\SFlash_CLI.exe gitlab package --package-name radar_fw --version 1.2.3 -o downloads
```

Download and flash in one go (PowerShell):

```powershell
$FW = .\SFlash_CLI.exe gitlab artifact --ref main --job build_fw -o downloads --print-firmware
.\SFlash_CLI.exe flash $FW --hardware vector --channel 0 --sequence suzuki --timeout 900
```

---

## 4. Putting every setting in one JSON file (`--config`)

Instead of typing a dozen arguments every time, put **all** of them in a JSON file and keep it in version control.

```json
{
  "comment": "Line 1 - left radar - bench 2",

  "file": ["C:\\builds\\firmware.s19"],

  "hardware": "vector",
  "channel": 0,
  "serial": 123456,
  "sequence": "suzuki",
  "radar-side": "s0",
  "bitrate": 500000,

  "security-dll": "C:\\tools\\SeedKey64.dll",
  "timeout": 900,

  "report": "report.html",
  "json-summary": "result.json",
  "trace-csv": "trace.csv",
  "quiet": true
}
```

```powershell
.\SFlash_CLI.exe flash --config line1_left.json
```

**The rules:**

- **A key is the long option name with the dashes dropped.** `--json-summary` becomes `"json-summary"` or `"json_summary"`; both work. Copying straight out of `--help` gives you the right key.
- **Every option of that command is supported**, including the ones a `.sfproj` cannot hold: `timeout`, `bitrate`, the report paths, `security-dll-signature`, `tx_id`/`rx_id`, and so on.
- **Precedence: a flag on the command line beats the config file, which beats `--project`, which beats the built-in default.** So one shared file can be overridden per run:

  ```powershell
  .\SFlash_CLI.exe flash --config line1.json --serial 999888
  ```

- **A misspelled key is an error (exit 2), never silently ignored** — and the message names the closest real key:

  ```
  Config error: Config file has 1 setting(s) that `flash` does not accept:
    'timeout_s' — did you mean 'timeout'?
  ```

  This matters: a `timeout_s` quietly dropped means that run has **no watchdog at all**, which is precisely the failure `--timeout` exists to prevent.

- **Values are validated exactly as on the command line.** `"tx_id": "0x77A"` becomes a number, `"tester_serial": "AABBCCDD"` becomes a byte string, and `"sequence": "bogus"` is rejected immediately.
- You may add a `"comment"` field — JSON has no comment syntax, so this key is ignored.
- Available on `flash`, `batch`, `parallel` and `test-connection`.

For `batch`/`parallel`, the ECU list can live in the same file:

```json
{
  "file": ["firmware.s19"],
  "hardware": "vector",
  "timeout": 900,
  "unit": [
    "name=Left,channel=0,serial=123456,side=s0",
    "name=Right,channel=1,serial=123456,side=s1"
  ]
}
```

> Windows paths in JSON: write `"C:\\tools\\SeedKey64.dll"` (doubled backslashes) or `"C:/tools/SeedKey64.dll"`.

> On/off flags: once `"quiet": true` is in the file it **cannot be turned off from the command line**; set `"quiet": false` in the file instead.

---

## 5. Common options

| Option | Meaning |
|---|---|
| `--hardware virtual\|vector` | `virtual` = simulated ECU (default), `vector` = real hardware |
| `--channel N` `--serial S` | Which hardware to use. **Always pass `--serial` as well** — it selects the physical device directly, bypassing the channel mapping in Vector Hardware Config. Take both values from `list-hardware`. |
| `--sequence suzuki\|generic` | Which flash sequence to run. Defaults to `suzuki`. |
| `--radar-side s0\|s1` | CAN ID preset for the Suzuki Radar. `s0` = Tx 0x77B/Rx 0x78B, `s1` = Tx 0x77A/Rx 0x78A |
| `--tx-id` `--rx-id` | Set the CAN IDs directly, overriding `--radar-side` |
| `--security-dll <path>` | The OEM's seed/key DLL. Omit it to use the built-in dummy algorithm. |
| `--timeout <seconds>` | **Always use this.** See section 7. |
| `--report x.html` | Full HTML report |
| `--json-summary x.json` | Machine-readable summary (for scripts/pipelines) |
| `--trace-csv x.csv` | The CAN/UDS trace table as CSV |
| `-v` / `-q` | Also print the TX/RX trace / print only the final result |
| `--dry-run` | Print the steps and exit without sending anything to the ECU |
| `--config x.json` | Load every setting from a JSON file (see section 4) |

Full list: `.\SFlash_CLI.exe flash --help`, `.\SFlash_CLI.exe batch --help`, and so on.

---

## 6. Exit codes

Use these in a script to decide whether to retry or stop.

| Code | Meaning | What to do |
|---|---|---|
| `0` | Success | continue |
| `1` | The ECU answered, but the flash failed | **Stop** — a genuine failure; retrying changes nothing |
| `2` | Bad arguments, bad file, bad configuration | Stop and fix the command |
| `3` | Could not reach the ECU | Retryable — check the cable, power, and whether the channel is taken |
| `4` | `--timeout` ran out | Retryable, but read the report first |
| `130` | Interrupted with Ctrl+C | — |

For `batch` and `parallel`, exit `0` only when **every** ECU passed.

In PowerShell the exit code is in `$LASTEXITCODE`:

```powershell
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --timeout 900
if ($LASTEXITCODE -ne 0) { Write-Error "Flash failed: $LASTEXITCODE"; exit $LASTEXITCODE }
```

---

## 7. `--timeout` — always use it for unattended runs

Without `--timeout` a run has **no upper bound on time**: while the ECU keeps answering "still working" (`0x78`), a single request can occupy up to **500 seconds**. An automated job then sits there until the system kills it — with **no report written** and the ECU left mid-session.

With `--timeout`, running out of time stops the tool properly: the bus is restored, **the report is still written**, and it exits with code `4`.

For `batch`/`parallel` this is the budget **per ECU**, not for the whole series.

Suggested values: `--timeout 120` for `test-connection`, `--timeout 900` for `flash` (adjust to your real firmware size).

---

## 8. Reports

```powershell
.\SFlash_CLI.exe flash firmware.s19 --hardware vector --channel 0 --timeout 900 ^
    --report report.html --json-summary result.json --trace-csv trace.csv
```

All three files are written **whether the run succeeds or fails** — a failure is exactly when you need them.

- **`report.html`** — open it in a browser: summary, firmware, every step, the full trace.
- **`result.json`** — for scripts. Contains `result`, `duration_seconds`, `ecu_info` (ECU serial and versions), `units` (for batch/parallel) and `unit_counts`.
- **`trace.csv`** — the complete TX/RX table, opens in Excel. This is the file to attach when asking for support.

The trace is captured in full **without** `-v`; that flag only decides whether it is also printed on screen.

> Note: the `suzuki` sequence deliberately contains no DID reads, so `ecu_info` is empty when you flash with it. To record the ECU's identity alongside such a flash, run `test-connection --json-summary` first, or use `batch` (which identifies every ECU).

---

## 9. Troubleshooting

### "Cannot load Security DLL ... the DLL is 32-bit (x86) but SFlash is running as 64-bit"

Your seed/key DLL is a 32-bit build. Windows **cannot** load a 32-bit DLL into a 64-bit process — that is an operating-system rule, and re-picking the file will not help.

→ Ask whoever supplies the DLL for the **x64 build**. They normally have both; the 32-bit one gets sent because it ships alongside the CANoe/CANape tooling.

### "the file exists, but Windows could not find one of the DLLs it depends on (WinError 126)"

Right architecture, but a DLL it depends on is missing.

→ Run `check-security-dll` to see what it needs, then copy those DLLs into the **same folder**, along with the matching Visual C++ Runtime.

### "Failed to load dynlib/dll ... not found when the application was frozen"

If you still see this sentence you are running an **old build**. Current builds unwrap that message and print the real cause.

### "ECU not reachable" (exit 3)

In order: CAN cable and ECU power → does `list-hardware` see the device → are `--channel`/`--serial` right → is another tool (CANoe/CANalyzer/CANape) holding the channel → are `--bitrate` and the CAN IDs (`--radar-side`) right for this ECU.

### "Security DLL returned error N", or the ECU rejects the key (NRC 0x35)

The DLL runs but does not produce a key the ECU accepts. Usually the `iVariant` string is missing — it tells a multi-ECU DLL which algorithm to apply:

```powershell
.\SFlash_CLI.exe flash firmware.s19 ... --security-dll-variant SUZ05
```

→ Ask the DLL's supplier for the correct value (or for confirmation that empty is correct).

### "called as a uint32 -> uint32 function ... but the ECU sent N bytes"

The DLL's calling contract is being read wrongly. The default `auto` is correct for every standard Vector DLL; only the older one-argument kind needs `--security-dll-signature uint32`. Run `check-security-dll` to see what it exports.

### "possible CAN bus conflict" warning

CANoe/CANalyzer/CANape is running, or the channel is in use by another application. The tool **only warns and continues**, so scripts are never blocked. If that other tool has a measurement running on the same channel, close it — its diagnostic traffic will collide with the flashing session.

---

## 10. Using it in a CI/CD pipeline

```yaml
flash_ecu:
  tags: [windows, vector-hw]
  variables:
    SFLASH_GITLAB_TOKEN: $CI_JOB_TOKEN
  script:
    - C:\tools\SFlash_CLI\SFlash_CLI.exe check-security-dll C:\tools\SeedKey64.dll
    - C:\tools\SFlash_CLI\SFlash_CLI.exe test-connection --config ci\identify.json
    - C:\tools\SFlash_CLI\SFlash_CLI.exe flash --config ci\flash_left.json
  artifacts:
    when: always
    paths: [flash_report.html, flash_result.json, trace.csv]
```

Four things worth getting right:

1. **Keep the settings in a `--config` file committed with the repo** — reviewable, diffable, and far better than a sprawling command line inside YAML.
2. **`artifacts: when: always`** — the reports matter most on the run that failed.
3. **Always set `--timeout`** (in the config file) — see section 7.
4. **Branch on the exit code, never on text in the log.** `3` and `4` are retryable; `1` is not.

---

## 11. Asking for support — what to send

1. The error message verbatim (copy it from the terminal; a screenshot only if you cannot).
2. `trace.csv` — the most useful one, it has every TX/RX frame.
3. `report.html` and `result.json`.
4. The output of `list-hardware` and of `check-security-dll <dll>`.
5. The command you ran, with all its arguments (or the `--config` file you used).
