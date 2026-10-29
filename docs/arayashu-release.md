# ChargeGrinder 3.5.6 Arayashu fork — prerelease

This is an independent fork based on upstream 3.5.5, not an upstream release.

## Fixes

- Hard/EXTREME no longer overwrites prepared Arayashu defense chains with Win Rate.
- Current UI mode is used immediately; no previous-run HoS state is consulted.
- Focused sacrifice fallback chooses Win Rate first, then overrides Ryoshu with defense.
- Defensive turns count only after submission; battle retries repeat setup.
- Missing defense detection and no observed sacrifice progress stop the bot.
- Arayashu automatic selling is disabled, including direct sale scanning.
- Fusion requires recognizing Spiderweb Entangled in Red in the full inventory.
- A second guard blocks fusion ingredients overlapping the protected gift.
- S3 shopping in Arayashu targets Ryoshu only.
- Each new run resets death/setup state and copies gift targets.
- Pack priority order and floor 6–15 assignments survive settings reload.
- Wishmaking no longer mutates shared fusion recipe lists.
- Failed Arayashu runs are limited per batch instead of retried indefinitely.

## Setup

Open Hard MD > Solo setup. Choose Deep (410 Starlight including one Spiderweb search)
or Economy (250). The preset selects one POISE/SLASH team, ten HoS Sinners, Starlight
reward priority, skipped secret bosses, no Wishmaking, and one initial run.
Set the matching game Identities yourself and name the game team POISE.
Leave LCB Meursault and G Corp. Gregor on the support bench, not deployed/backups.
The preset cannot equip game Identities or confirm a gift hidden outside the UI.

Adversities can be All or None. None is easier but does not earn Adversity rewards.
The default failure limit is two failed runs per batch; setup recognition errors stop
immediately and do not automatically buy another start.

## Validation and limits

Automated regression tests mock game I/O. Compilation, binary metadata and SHA-256
checks are part of the release workflow. These checks do not constitute a live
Limbus Company floor-15 playtest. Focused portrait matching and submission timing
still require verification in the actual game. Start with one observed run.

If Spiderweb cannot be recognized, the program stops rather than fusing an unknown
inventory. No gifts are automatically sold in Arayashu, so Cost shortages may delay
purchases. Resume only after checking the gift and display settings.

Generic event handling is still inherited from upstream; do not accept Record Logs
of That Day in a solo run. This prerelease does not claim to identify every cursed
event or guarantee completion. Automatic Gift Search still relies on the game's
screen layout: verify the starting Spiderweb gift.

The original Windows input backend and its prerequisites are unchanged.
Use app.exe normally; app_debug.exe also shows diagnostic console output.
