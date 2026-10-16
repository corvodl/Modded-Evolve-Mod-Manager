# Local client profile details

After entering the protected Developer tab, the Account Profile Inspector opens
on a Client details page. It checks `profile.json` beside the explicitly
configured launcher executable, then the known
`%LOCALAPPDATA%/ModdedEvolveLauncher/profile.json` cache. Available locations are
listed separately; the first existing location is selected. The local cache is
labelled independently because it need not belong to a portable configured
client. Refresh rereads the selected source and clears stale rows on failure.

Displayed fields are limited to display name, title, badge/banner identifiers,
level, experience, games played, wins/losses, favorite hunter/monster, and the
client's update timestamp. Missing fields remain absent. The reader accepts at
most 1 MiB and never reads authentication, identity or session stores. Unknown
fields are not exposed. These are cached local values, not verified server data.

Automatic discovery is read-only. Existing readable Ownables/XPProgression
backups still use the explicit backup picker, comparison and new-copy export.
Encoded TRSEVLVPC data is not decoded, and ownership, balances and live account
edits are not inferred from launcher summary fields. No profile values or paths
are persisted by the new panel. The dialog is disposed when closed.
