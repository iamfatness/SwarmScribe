# SwarmScribe — Per-Channel Transcription Spec

Date: 2026-10-03
Status: Approved design, written for review
Parent: `2026-10-02-swarmscribe-architecture-design.md` (master spec).
First sub-project of SIPREC call recording (approach A: Kamailio SIP edge,
rtpengine media, SwarmScribe call controller). Useful on its own for any
stereo recording where each channel is a different speaker.

## 1. Purpose

Call recordings are stored as one stereo file with one party on the left
channel and the other on the right. Transcribing each channel separately
attributes every line to the right speaker without guessing. Today the engine
mixes everything to mono.

### Success criteria

- A stereo recording transcribed in split mode produces one transcript in
  which every segment is labelled with its speaker, merged in time order.
- Mono behaviour is unchanged and remains the default.
- Vocabulary biasing and corrections apply to both channels exactly as they
  do to a mono recording.
- One model load serves both channels.

### Out of scope

Speaker diarization of a single channel; more than two channels; live
transcription.

## 2. Channel modes

| Mode | Behaviour |
|---|---|
| `mono` | Default. Channels are mixed, as today. |
| `stereo_split` | The file must be stereo; left and right are transcribed separately. A mono file is an `undecodable`-class failure with a clear message. |
| `auto` | Split if the file has two channels, otherwise mono. |

`channel_labels` names the two channels, default `["Left", "Right"]`.
Labels are 1–40 characters, exactly two of them, and only used when splitting.

## 3. Protocol changes

`PROTOCOL_VERSION` stays `1` (not shipped); the schema snapshot is
regenerated.

- `JobSettings.channel_mode: Literal["mono", "stereo_split", "auto"] = "mono"`
- `JobSettings.channel_labels: tuple[str, str] = ("Left", "Right")`
- `Segment.channel: int | None = None` — `0` left, `1` right; `None` for mono.
- `SegmentsDocument.channel_labels: list[str] | None` — the labels used when
  the transcript was split, else `None`.

## 4. Engine

- `TranscribeSettings` gains `channel_mode` and `channel_labels` with the
  same defaults and validation.
- Decoding: faster-whisper's `decode_audio(..., split_stereo=True)` returns
  the two channels as separate arrays at 16 kHz. In `auto` mode the channel
  count is read first (PyAV) to decide.
- Each channel is transcribed with the same loaded model, the fixed
  settings, the same hotwords, and the same temperature ladder; corrections
  are applied to each channel's segments. `corrections_applied` sums the
  counts across channels.
- Segments from both channels are merged into one list ordered by
  `(start, channel)`; each carries its `channel`.
- `duration` is the file's duration (both channels are the same length).
- Decode failures and non-stereo input in `stereo_split` mode raise
  `UndecodableAudioError`.

### Outputs

- `txt`: one line per segment; in split mode each line is prefixed
  `<label>: `.
- `srt`: one cue per segment; in split mode the cue text is prefixed
  `<label>: `.
- `segments.json`: segments carry `channel`; the document carries
  `channel_labels`.
- Mono outputs are byte-for-byte unchanged from today.

### CLI

`swarmscribe-engine FILE --out DIR [--channels mono|stereo-split|auto]
[--labels "Agent,Customer"]`. `--labels` without a split mode is an error.

## 5. Leader

- `storage_locations` gains `channel_mode` (default `mono`) and
  `channel_labels` (default `["Left", "Right"]`); migration `0003`.
- The claim's `JobSettings` carries the location's mode and labels.
- A2's `locations add` gains `--channels` and `--labels`. A SIPREC calls
  location will be `stereo_split` with labels taken from the call (a later
  sub-project may set labels per call; until then they are per location).

## 6. Testing

- Generated stereo WAV with different tones (or silence) per side: split
  mode yields segments only from the channel with content, correctly
  labelled.
- Fake-model tests: merge order, ties broken by channel, labels in txt/srt,
  corrections counted across channels, hotwords passed for both passes, one
  model load.
- `stereo_split` on a mono file → `UndecodableAudioError`; `auto` on mono →
  identical output to `mono`.
- Protocol: snapshot regenerated; `channel` optional; label validation.
- Leader: claim carries the location's channel settings.
- Smoke (real `tiny.en`): a generated stereo file in split mode completes and
  validates against the protocol.

## 7. Build order

One plan, after Plan A2's locations commands exist (the leader part depends
on `locations add`): protocol → engine → CLI → leader migration and claim.
