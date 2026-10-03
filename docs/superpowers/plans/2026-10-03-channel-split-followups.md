# Per-channel transcription (Tasks 1–5) — carried-forward items

These come from the task reviews and the final review of `channel-split` (2026-10-03). None of them blocks merge.
Tasks 6–7 (the leader side) are still to be built from the plan.

## For Tasks 6–7

- The leader decides whether a split happened from the presence of `document.channel_labels`, not from
  `settings.channel_mode`. `settings` echoes the request, and an `auto` job on a mono file is never split.

## Can wait

- **No audio stream in mono mode.** A mono run on a file with no audio stream ends in an `IndexError` traceback
  instead of `UndecodableAudioError`. This was already true before the branch. `auto` and `stereo_split` are fixed.
- **Two-channel layouts that are not stereo** (for example dual-mono) are remixed by the decoder before the split.
- **Corrupt frames** are skipped silently in split mode. Mono behaves the same way.
- **Memory.** Splitting decodes both channels: about twice the memory of mono (roughly 460 MB held and 1.2 GB peak per
  hour). This is documented in the README; follower sizing should allow for it.
