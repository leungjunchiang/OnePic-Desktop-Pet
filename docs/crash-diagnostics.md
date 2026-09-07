# Lili crash diagnostics

Lili writes bounded Python and lifecycle diagnostics under `%LOCALAPPDATA%\Lili`.
Starting with v0.23.219, `native-crash.log` also uses Python's `faulthandler`
to capture native faults where the runtime can still write a traceback. This
does not make Qt/C++ faults catchable by `try/except`.

For an intermittent Windows native crash, Windows Error Reporting can retain a
process dump. Create these values under
`HKCU\Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\Lili.exe`:

- `DumpFolder` (expandable string): a user-writable diagnostic directory;
- `DumpType` (DWORD): `2` for a full dump;
- `DumpCount` (DWORD): a small bounded value such as `3`.

Remove that per-application key after reproducing the crash. Dumps may contain
in-memory private data, so do not publish them; share one only through a private
support channel together with `runtime.log`, `native-crash.log`, and
`diagnostics\lifecycle.log`.
