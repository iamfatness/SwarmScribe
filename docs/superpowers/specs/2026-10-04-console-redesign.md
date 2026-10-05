# Fleet console redesign: the Ink console

Date: 2026-10-04. Status: approved direction, ready to build.
Scope: `packages/console-web` only. Presentation, wording and the navigation frame.

Implementation plans:

- `docs/superpowers/plans/2026-10-04-console-redesign-r1-shell-and-fleet.md`
- `docs/superpowers/plans/2026-10-04-console-redesign-r2-leader-and-admin.md`

## 1. Purpose

The owner's words: "We need to build out a modern UI/UX for it. What I've seen so far isn't
great and doesn't really match the brand and voice we have been building."

The console works and is accessible, but it looks like a default admin page and speaks in
the protocol's words (poll, queued, leased, grant). This redesign makes it look and read
like SwarmScribe: honey amber on warm ink, paper sheets, serif headings, short sentences
that say what is happening.

The owner was shown three directions, chose **B, "Ink console"**, then saw four more screens
in that language and approved them.

### Success criteria

1. Every screen matches its approved mockup in layout, colour, type and wording, except
   where section 10 says why not.
2. The dark Ink console is the designed default. A light theme exists, follows the system
   setting and the Theme switch, and is designed, not derived.
3. Every user-visible string is the one in section 6.
4. Nothing about behaviour changes: section 8.
5. Accessibility is at least what it was: section 7. Zero axe violations (WCAG 2.1 A and
   AA) on every page and dialog in both themes; every text pair at 4.5:1 or better.
6. The console's CSP still holds: no inline style, no font or image from another origin,
   hashed assets only. `npm run build` passes `scripts/check-dist.mjs` unchanged.
7. Every existing unit and end-to-end test still exists and passes, with selectors and
   expected text updated only where structure or wording changed.
8. A person has looked at screenshots of every screen in both themes at 1280 and 768
   pixels wide before each plan is called done.

## 2. Sources

### Approved mockups (the visual authority)

Static HTML with inline styles, in `docs/superpowers/design/`:

| File | Screen |
| --- | --- |
| `docs/superpowers/design/InkConsole.dc.html` | Fleet overview. The chosen direction. |
| `docs/superpowers/design/LeaderJobs.dc.html` | A leader's page, Jobs tab. |
| `docs/superpowers/design/TokenDialog.dc.html` | The join token, shown once. |
| `docs/superpowers/design/AdminLeaders.dc.html` | Administration, Leaders. |
| `docs/superpowers/design/SignIn.dc.html` | Sign-in. |

Not chosen, kept for reference only: `docs/superpowers/design/Main.dc.html` and
`docs/superpowers/design/PaperDesk.dc.html`. The light theme borrows PaperDesk's ground
(paper page, ink header, ink callout with the amber shadow) and nothing else.

The mockups' inline styles cannot ship: the console's CSP is `style-src 'self'` and lint
forbids `style` props. Every value in them becomes a token or a class.

### Brand

The public site, read only: `IamfatnessWebsite/sites/swarmscribe/public` (`assets/site.css`,
`assets/logo.svg`, `assets/logo-lockup.svg`, `assets/favicon.svg`, `index.html`).

- Honey amber `#f5b83d` on warm ink `#14110d`; paper `#f3ead9`.
- Wordmark: "Swarm" in bold sans, "Scribe" in italic serif amber.
- Serif headings (Iowan, Palatino, Georgia), system sans body, monospace figures. System
  font stacks only. No web fonts.
- Hexagon pips as marks.
- The paper "ledger" card with a hard amber offset shadow.
- Voice: plain and direct. Short sentences that say what is happening.

## 3. Design tokens

All in `src/styles/tokens.css`. A colour is written nowhere else, with one exception: the
logo's two brand constants in `src/styles/shell.css`.

### 3.1 Two colour sets

There are two sets of colour tokens with the same names. A surface picks a set, and
everything inside it reads the same token names.

| Where | Dark theme (default) | Light theme |
| --- | --- | --- |
| The page | ink | paper |
| `.on-ink`: the rail, the sign-in brand panel | ink | ink |
| `.sheet`: dialogs, callouts, "Needs a look", the sign-in card | paper | ink |

So a sheet is always the opposite of the page it sits on. Buttons, fields, links and error
panels inside a sheet need no special rules: they read the sheet's set.

Theme selection is unchanged in mechanism (`src/app/theme.ts` sets `data-theme` on
`<html>`, or leaves it off to follow the system). The stylesheet's base is now the dark
set, and `prefers-color-scheme: light` gives the light theme unless `data-theme="dark"`.
In practice a browser always reports light or dark, so with Theme on "System" the console
is light on a light system and dark on a dark one, as before. See O6.

### 3.2 Colour

| Token | Ink set | Paper set | Used for |
| --- | --- | --- | --- |
| `--ground` | `#14110d` | `#f3ead9` | Page background; a sheet's own background |
| `--panel` | `#1c1813` | `#fffaf0` | Rail, cards, tables, stat tiles, form panels |
| `--panel-2` | `#26211a` | `#e7dcc6` | Hover on ghost buttons, pills and nav links |
| `--text` | `#f3ead9` | `#1c1712` | Body text |
| `--muted` | `#b5a993` | `#5c5243` | Secondary text, column headers, labels |
| `--line` | `rgb(243 234 217 / 0.14)` | `rgb(28 23 18 / 0.16)` | Hairlines (decorative) |
| `--edge` | `#7d7566` | `#5c5243` | Edges of fields, ghost buttons, pills, switched-off buttons |
| `--accent` | `#f5b83d` | `#855400` | Marks, chart line, current-tab rule, notice rule |
| `--accent-text` | `#f5b83d` | `#6b4300` | Links, code, "With follower" state, token status |
| `--accent-hover` | `#ffd27a` | `#3f2800` | Link hover |
| `--accent-wash` | `#3a2e19` | `#e6d8bf` | Current nav item, notice background |
| `--primary-bg` | `#f5b83d` | `#14110d` | Primary button, pressed pill |
| `--primary-fg` | `#14110d` | `#f3ead9` | Text on the above |
| `--bad` | `#ff9d8f` | `#8a1c14` | Not answering, failed, danger buttons, field errors |
| `--bad-wash` | `#372822` | `#ebdac9` | Error panel background, danger hover |
| `--field-bg` | `#100d0a` | `#fffaf0` | Inputs, selects, textareas |
| `--mark-quiet` | `#5c5243` | `#b5a993` | Hexagon beside a nav item that is not current (decorative) |
| `--focus` | `#f5b83d` | `#855400` | Focus ring |

Not themed: `--amber: #f5b83d` (logo, the offset shadow, the skip link). The logo's nib is
`#14110d` in both themes.

The mockups use `rgba()` washes. The tokens are the same colours composited to solid hex
(`--accent-wash` is amber at 14% over ink-2), so the contrast test can measure them.

Measured contrast (WCAG 2.x ratio). Text needs 4.5:1; marks, focus rings and control edges
need 3:1.

| Pair | Ink | Paper |
| --- | --- | --- |
| `--text` on `--ground` / `--panel` / `--panel-2` | 15.8 / 14.8 / 13.4 | 14.9 / 17.1 / 13.1 |
| `--text` on `--accent-wash` / `--bad-wash` / `--field-bg` | 11.1 / 11.8 / 16.2 | 12.7 / 13.0 / 17.1 |
| `--muted` on `--ground` / `--panel` / `--panel-2` / `--accent-wash` | 8.1 / 7.6 / 6.9 / 5.7 | 6.4 / 7.4 / 5.6 / 5.4 |
| `--accent-text` on `--ground` / `--panel` / `--panel-2` | 10.6 / 9.9 / 9.0 | 7.2 / 8.3 / 6.4 |
| `--bad` on `--ground` / `--panel` / `--panel-2` / `--bad-wash` | 9.4 / 8.8 / 8.0 / 7.0 | 7.8 / 8.9 / 6.8 / 6.8 |
| `--primary-fg` on `--primary-bg` | 10.6 | 15.8 |
| `--accent`, `--focus` against `--ground` / `--panel` | 10.6 / 9.9 | 5.4 / 6.2 |
| `--edge` against `--ground` / `--panel` / `--field-bg` | 4.1 / 3.9 / 4.3 | 6.4 / 7.4 / 7.4 |

`src/styles/tokens.test.ts` computes these from the stylesheet and fails below the
thresholds. It also fails if the two written copies of a set differ (each set is written
once plainly and once inside the `prefers-color-scheme` block).

### 3.3 Type

Three system stacks, no web fonts:

| Token | Stack | Used for |
| --- | --- | --- |
| `--serif` | `"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif` | Page titles, sheet titles, the sign-in line |
| `--sans` | `"Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif` | Everything else |
| `--mono` | `ui-monospace, "Cascadia Mono", Consolas, Menlo, monospace` | Figures, ids, labels, paths, addresses |
| `--wordmark-serif` | `Georgia, "Times New Roman", serif` | "Scribe" in the wordmark |

| Token | Size | Used for |
| --- | --- | --- |
| `--fs-display` | `clamp(2rem, 4.2vw, 3rem)` | The sign-in line (48px at 1280) |
| `--fs-h1` | `2.125rem` (34px), serif, weight 400, line-height 1.1, tracking -0.015em | Page title |
| `--fs-sheet-title` | `1.75rem` (28px), serif | Dialog title, sign-in card title |
| `--fs-callout-title` | `1.5rem` (24px), serif | "Needs a look", callout title |
| `--fs-h2` | `1.25rem` (20px), sans 600 | Leader card name |
| `--fs-h3` | `1.125rem` (18px), sans 600 | Section heading (`h2` elements inside a page) |
| `--fs-body` | `0.9375rem` (15px), line-height 1.5 | Body |
| `--fs-small` | `0.875rem` (14px) | Card footers, rail footer, field labels |
| `--fs-fine` | `0.8125rem` (13px) | Cell notes, stat labels, status pills |
| `--fs-mono-fine` | `0.78125rem` (12.5px) | Labels, table headers, figure labels |
| `--fs-figure` | `1.875rem` (30px), mono | Totals |
| `--fs-figure-small` | `1.125rem` (18px), mono | Figures on a leader card |

Sizes are in `rem`, so the page follows the browser's text size.

### 3.4 Space, shape, shadow, size

| Token | Value |
| --- | --- |
| `--s-1` to `--s-10` | 4, 8, 12, 14, 16, 20, 24, 28, 36, 56 px |
| `--r-sm` | 4px (token field, notice) |
| `--r` | 6px (buttons, fields, nav items, sheets) |
| `--r-lg` | 8px (panels, cards, tables) |
| `--r-pill` | 999px (pills, status pills) |
| `--shadow-offset` | `8px 8px 0 var(--amber)` |
| `--shadow-drop` | `0 30px 60px -30px rgb(0 0 0 / 0.8)` |
| `--h-control` | 44px (buttons, pills, fields, nav links, tabs) |
| `--h-row` | 36px (a button inside a table row) |
| `--rail-width` | 248px |

The mockups use a few in-between values (22, 26, 30). Where a token is within 2px the
token is used; the handful of exact values the layout depends on are written as numbers
beside the rule they belong to.

Breakpoints: 900px (the rail becomes a top bar; row actions stack), 640px (cards and totals
drop a column), 480px (the token dialog stacks).

## 4. Components

Class names that already exist keep their names and get the new look, so markup that is
not restructured needs no change.

### 4.1 Rail (`.rail`, `components/Layout.tsx`)

A `<header class="rail on-ink">`, 248px wide, sticky for the full height of the window,
`--panel` background with a hairline on its right. Top to bottom:

1. The brand: the mark (30px, inline SVG) and the wordmark, a link to `/`.
2. `<nav aria-label="Console">`: **Fleet** (hexagon pip); beneath it, indented, one link per
   leader the person can see, in the fleet's own order; then, after a gap, **Administration**
   (hexagon pip), only for a console administrator.
3. At the bottom, above a hairline: the person's email, "Console administrator" if they are
   one, a line saying which roles they hold, the Theme switch, and a full-width Sign out.

A nav link is 44px tall, muted. The current one has the `--accent-wash` background, text
colour and weight 600, and `aria-current="page"`. Fleet is current only on `/`; a leader's
link is current on any of that leader's tabs; Administration on any `/admin` address. The
pip is amber when its item is current and `--mark-quiet` otherwise.

A leader that needs attention carries a small flag on the right of its link: "no answer"
(`--bad`) when unreachable, "revoked" (`--bad`) when its credential is revoked, "off"
(muted) when switched off. The flag is part of the link's text.

With many leaders the list of leaders scrolls inside the rail; the person block (email,
Theme, Sign out) stays pinned at the bottom.

### 4.2 Top bar (narrow widths)

At 900px and below the rail is a bar across the top, sticky so it stays in view as the page
scrolls: the brand on the left and a **Menu** button on the right (`aria-expanded`,
`aria-controls`). Everything else in the rail (the nav, the person, Theme, Sign out) is
inside the controlled panel, which is `display: none` until Menu is pressed. It is a
disclosure, not a dialog: the page behind it stays in view but does not scroll while it is
open. Opening it moves focus into the
menu (to its first link). Escape closes it and returns focus to the Menu button. A click
outside it closes it and returns focus to the Menu button. Following any link in it closes
it.

### 4.3 Page header

Serif `h1`. Beneath it an optional muted line (`.page-sub`, at most 64 characters wide).
To its right, on the same row when there is room, the page's own controls or status.
A leader's page has a breadcrumb line above ("Fleet / eu-1") and its labels in mono beside
the title.

### 4.4 Pills (`.pill`)

Filter choices: toggle buttons with `aria-pressed`, in a `role="group"` with a label. 44px
tall, fully rounded, `--edge` outline. Pressed: `--primary-bg` fill and `--primary-fg` text,
weight 600. A count, when there is one, follows the label in mono. A label longer than the
page is wide wraps inside its pill; the page never grows to fit it.

### 4.5 Stat tile (`.stat-tile`)

A `--panel` box with a 13px muted label over a 30px mono figure. Four across, two across
below 640px. Marked up as a `<dl>`.

### 4.6 Leader card (`.leader-card`)

An `<article>` named by its heading, on `--panel`, radius 8px, padding 20px by 22px.

- Head: the leader's name as an `h2` holding a link to the leader; its labels beneath in
  mono; the status pill on the right.
- Answering: the chart, then four figures in a row (`<dl>`), then a footer line above a
  hairline.
- Not answering, revoked, switched off or not yet checked: no chart and no figures. A
  sentence saying what happened, a muted sentence with what is still known, the last error
  if any, and a link to what the leader last reported. A not-answering or revoked card has
  a `--bad` border.

### 4.7 Sparkline (`.sparkline`, `components/Sparkline.tsx`)

Inline SVG, `role="img"`, named by a `<title>` that says the same thing in words. The
drawing is 400 by 64 units, stretched to the card's width with
`preserveAspectRatio="none"`; strokes use `vector-effect: non-scaling-stroke`, so the line
is 2px at any width. A lone point is a zero-length round-capped stroke (a circle would
stretch into an ellipse). Five-minute periods with no answer are marked by small `--bad`
blocks on the baseline: shape and position, not colour alone.

### 4.8 Panel and table

Tables sit in `.table-scroll`: a `--panel` box, radius 8px, hairline border, that scrolls
sideways on its own (`role="region"`, a name, `tabindex="0"`). Column headers are 12.5px,
muted, weight 600, never wrapped. Rows are separated by hairlines. A row header is weight
400 and never wraps (a hyphen in "eu-1" is not a place to break). Cells wrap at word
boundaries only; a cell of long unbroken text opts in to breaking anywhere with `.long`.
A second line inside a cell is `.cell-note`: 13px, muted.

**The Actions column is pinned.** `td.actions` and its header are `position: sticky;
right: 0` inside the scrolling region, with the panel's background and a hairline on the
left. However far the table is scrolled, a row's buttons are in reach. At 900px and below
a row's buttons stack, so the pinned column is one button wide.

### 4.9 Buttons (`.button`)

| Kind | Look |
| --- | --- |
| Ghost (the default) | Transparent, `--text`, 1px `--edge`. Hover: `--panel-2`. |
| Primary (`.button-primary`) | `--primary-bg` fill, `--primary-fg` text, weight 600. Hover: underline. Amber on ink; ink on paper. |
| Danger (`.button-danger`) | Transparent, `--bad` text and edge. Hover: `--bad-wash`. |
| Switched off (`:disabled`, `aria-disabled="true"`) | Dashed `--edge`, muted text, `not-allowed` cursor. Beside it, in words, the role it needs. |

44px tall; 36px inside a table row. One primary button per view at most: the thing the
view is for (Add a leader, Create join token, Add location, Try again on a failed job,
Copy in the token dialog, the first sign-in provider).

A button whose visible text needs its row for context has a longer accessible name, and
that name always contains the visible text word for word ("Try again" is named "Try again:
job 1a2b3c4d").

### 4.10 Sheet (`.sheet`), dialog and callout

A sheet is a surface in the opposite colour set: paper on the dark page, ink on the light
one. Radius 6px.

- **Dialog** (`.dialog.sheet.sheet-lifted`): a native `<dialog>` opened with `showModal()`,
  560px wide at most, with the drop shadow and the hard amber offset shadow. Serif title.
  Buttons right-aligned at the bottom. The backdrop is ink at 65%.
- **Sign-in card**: the same lifted sheet, 400px wide.
- **Callout** (`.sheet.callout`) and **"Needs a look"**: a flat sheet, no shadow, as the
  mockups draw them.

### 4.11 Form fields

Inputs, selects and textareas are 44px tall with a 1px `--edge` border on `--field-bg`.
A label sits above its field, 14px, weight 600. Help text is 13px, muted, tied to the field
with `aria-describedby`. A field in error has a 2px `--bad` border, `aria-invalid`, and its
message beneath in `--bad`, in a `role="alert"`.

### 4.12 Status marks and the status pill

- **Hexagon** (`.mark-hex`): 9 by 10px, `--accent`, cut with `clip-path`. Means "fine" or
  "current". A quiet variant in `--mark-quiet`.
- **Ring** (`.mark-ring`): an 11px circle outline. `--bad` for not answering; muted for
  switched off or not yet checked.
- **Status pill** (`.status-pill`): a mark and words in a rounded outline. Answering:
  amber edge, hexagon. Answering with missed checks, or not answering yet: dashed amber
  edge, quiet hexagon. Not answering, credential revoked: `--bad` edge and text, ring.
  Waiting for the first check, switched off: muted, ring.

Marks are `aria-hidden`; the words always say it. In Windows high-contrast mode the hexagon
falls back to a filled square with a border, since backgrounds are dropped.

### 4.13 Lines that report

- **Notice** (`.notice`): `--accent-wash`, a 3px `--accent` rule on the left. A leader's
  health note; the idle notice; a failed refresh.
- **Error panel** (`.error-panel`, `role="alert"`): `--bad-wash`, a 3px `--bad` rule. Our
  title in `--bad`, weight 600; the server's own text beneath in `--text`; a retry button
  when there is something to retry.
- **Result of the last action** (`.action-notice`, `role="status"`): one line, weight 600,
  led by a hexagon when it has text. It keeps its height when empty so the page does not
  jump. There are no toasts: nothing appears and disappears on a timer.

## 5. Screens

### 5.1 Frame

`.app` is a two-column grid: the rail (248px) and the main column. The skip link is the
first focusable thing and lands on `<main id="main">`. The idle notice, when shown, is at
the top of the main column. `main` has 30px by 36px padding and is at most 1400px wide.
At 900px and below: one column, the top bar, 24px by 16px padding.

### 5.2 Fleet overview (`/`)

Mockup: `docs/superpowers/design/InkConsole.dc.html`.

1. Header: "Fleet"; beneath, how many leaders and when they were last checked; to the
   right, the label filter as pills ("All leaders", then one pill per `key = value`).
   With more than six label pairs the pills are replaced by the select the page had before,
   labelled "Label".
2. Totals: four stat tiles for the leaders shown: Waiting now; Finished, last hour;
   Finished, last day; Followers at work. A dash when no shown leader has figures. If any
   shown leader is not answering, a line beneath says the totals include its last figures.
3. A two-column grid. First, when there is anything to say, the **"Needs a look"** sheet;
   then one card per leader shown. One column below 640px.

"Needs a look" lists, for the leaders shown:

- each location a leader could not scan, with the leader's own error text, linking to that
  leader's Locations tab;
- a leader that revoked the console's credential;
- how many tries failed in the last day, and on which leaders.

It lists at most five items; when there are more, a plain line beneath counts the rest
("And 3 more things to look at."). A leader's name inside it never breaks at its hyphen. The
sheet is as tall as the card beside it, so it never leaves a hole under itself. It is not shown when
there is nothing to list.

### 5.3 Sign-in (`/sign-in`)

Mockup: `docs/superpowers/design/SignIn.dc.html`.

Two halves, each half the window. Left, on ink in both themes: the brand (40px mark), the
line "Every leader you look after, in one place." in display serif with a muted paragraph
beneath, and at the bottom a hexagon and this console's host. Right, on `--panel`: the
sign-in card, a lifted sheet, centred. The card holds the `h1` "Sign in", one line of
guidance, any status ("You are signed out."), one full-width 48px button per provider (the
first primary, the rest ghost), and a note about being signed out. At 900px and below the
halves stack, brand first.

### 5.4 A leader's page (`/leaders/<name>/<tab>`)

Mockup: `docs/superpowers/design/LeaderJobs.dc.html`.

Breadcrumb; the name as `h1` with its labels beside it and the status pill on the right; a
line saying the person's role here and what that switches off; a notice if the leader is
not answering; the five tabs as links with a 2px amber rule under the current one; then
the tab's own section with its `h2`.

- **Jobs.** A bar with state pills on the left ("All", "Waiting", "Being worked on",
  "Failed", "Finished", "Cancelled", each with its count) and, on the right, the Location
  select, "Loaded at" and Refresh. Then the table: Job, State, Recording, Pool, Priority,
  Tries, Queued, Actions. State is words, coloured by kind (amber while a follower has it,
  `--bad` when failed, muted when finished or cancelled). Recording is the file's key in
  mono with a note beneath: where it came from, then why it failed or who cancelled it.
  "Try again" is the primary button on a failed job and a ghost button on a cancelled one.
- **Pools and followers.** Pools table (from the last check), then Followers with "Loaded
  at" and Refresh beside its heading.
- **Locations.** "Add location" (primary) on the left, "Loaded at" and Refresh on the
  right. Table: Location, Folder, Pool, Scanning, Last scan, Actions. Folder carries where
  it looks and the channel layout as notes; Pool carries the device; Scanning carries how
  often.
- **Join tokens.** "Create join token" (primary), "Loaded at", Refresh, the table.
- **Consent report.** A line of explanation with Refresh, then the two tables.

### 5.5 The join token, shown once

Mockup: `docs/superpowers/design/TokenDialog.dc.html`.

A lifted sheet. Title: "Here is the join token. It is shown once." A muted paragraph that
names the pool and says nobody can read it again. The token in a 48px read-only mono field
with a 2px edge, and a primary **Copy** button beside it. A status line beneath ("Not
copied yet."). Then three facts in a row above a hairline: Pool; Can be used; Expires. At
the bottom right, a ghost **I have stored it**.

### 5.6 Administration (`/admin/leaders`, `/admin/grants`, `/admin/admins`)

Mockup: `docs/superpowers/design/AdminLeaders.dc.html`.

One page with three sections, each its own address. `h1` "Administration", a paragraph on
what console administrators are for, then the sections as tab links: Leaders, Who can do
what, Console administrators.

- **Leaders.** An `h2` that counts them ("3 leaders") with **Add a leader** (primary) on
  the right; the table (Leader, Address, Labels, State, Credential, Actions); then a flat
  sheet callout, "Adding a leader takes two steps".
- **Who can do what.** `h2`, a paragraph on how roles are worked out, the table (Who,
  Role, On which leaders, Given, Actions), then the "Give a role" form in a panel.
- **Console administrators.** `h2`, the table (Who, Added, Actions), then the "Add a
  console administrator" form in a panel.

### 5.7 Everything else

- **Other dialogs** (confirmations, set priority, add location, create token, add or edit a
  leader, replace a credential): the same lifted sheet. A confirmation's safe choice, "No,
  go back", comes first and takes focus; the action is a danger button.
- **Not found**, **loading** and **first-load failure**: the page header and one line or
  an error panel. No new layout.

## 6. Copy

### 6.1 The console's words

The leader's API speaks the protocol. The console speaks to a person.

| The protocol says | The console says |
| --- | --- |
| poll, polled | check, checked |
| reachable / unreachable | answering / not answering |
| queued | waiting |
| leased | being worked on; "with follower 1a2b3c4d" |
| completed | finished |
| retry | try again |
| attempts | tries |
| draining | winding down |
| active (follower) | at work |
| enabled / disabled | on, switched on / switched off |
| grant, scope, principal | a role, given on some leaders, to someone |
| rotate (a credential) | replace |
| URL (a leader's) | address |

Rules:

- Say what is happening, in a short sentence, with a full stop.
- Never show a protocol word from the left column. Addresses keep them (`?state=queued`).
- A count under ten that reads as part of a sentence is a word ("after three tries"). A
  figure is a numeral.
- The leader's own error text is shown as it is sent, as text, never rewritten.
- Loading lines begin with "Loading" (the end-to-end tests wait for them to go).
- "Revoke", "Create", "Scan", "Pool", "Location", "Consent" and "Join token" are kept:
  they are the product's own nouns and verbs.

### 6.2 Every string

"Same" means the wording does not change. `{…}` is a value.

#### Frame (`components/Layout.tsx`, `app/session.tsx`, `app/usePageTitle.ts`)

| Was | Is |
| --- | --- |
| Brand link text "SwarmScribe console" | The wordmark "SwarmScribe"; the link's name is "SwarmScribe console" |
| Navigation name "Main" | "Console" |
| "Fleet" | Same |
| (none) | Each leader's name under Fleet; the list's name is "Leaders" |
| (none) | Flag beside a leader: "no answer", "revoked", "off" |
| "Administration" | Same |
| (none) | "Menu" (the top bar's button) |
| `{email}` | Same |
| (none) | "Console administrator" (for one) |
| (none) | "Admin on 2 leaders" / "Admin on 2, viewer on 1 leader" / "No role on any leader yet" |
| "Theme", "System", "Light", "Dark" | Same |
| "Sign out" | Same |
| "Skip to main content" | Same |
| "Updates are paused because you have been inactive. Press any key or click to resume." | "Checks are paused because you have been away. Press a key or click to start them again." |
| "Loading the console…" | Same |
| "SwarmScribe console" (heading when the session cannot load) | Same |
| Browser tab: "{page} · SwarmScribe console" | Same |
| Browser tab pages "Leaders", "Grants", "Console administrators" | "Administration: Leaders", "Administration: Who can do what", "Administration: Console administrators" |

#### Health (`components/HealthBadge.tsx`)

| Was | Is |
| --- | --- |
| "Reachable" | "Answering" |
| "Reachable (1 failed poll)" / "Reachable ({n} failed polls)" | "Answering, 1 missed check" / "Answering, {n} missed checks" |
| "Waiting for first poll" | "Waiting for the first check" |
| "Not answering yet (1 failed poll)" / "… ({n} failed polls)" | "Not answering yet, one try" / "Not answering yet, {n in words} tries" |
| "Unreachable" | "Not answering" |
| "Credential revoked" | Same |
| "Disabled in the console" | "Switched off" |

#### Fleet overview (`pages/FleetPage.tsx`, `components/Sparkline.tsx`, `components/ThroughputChart.tsx`)

| Was | Is |
| --- | --- |
| "Fleet" | Same |
| "Loading the fleet…" | Same |
| "{n} leader" / "{n} leaders" / "{n} of {m} leaders" | Same |
| "Updated {time}" | "· Checked at {time}, and every 10 s" |
| "Could not refresh the fleet: {error title} Showing the last figures." | "The last check did not work: {error title} These are the last figures." |
| "You hold no role on any leader yet. Ask a console administrator for a grant." | "You have no role on any leader yet. Ask a console administrator to give you one." |
| "No leader has the label {label}." | Same |
| Filter label "Label"; "All leaders"; options "{key}={value}" | Pills in a group named "Show leaders with the label": "All leaders", "{key} = {value}". With more than six pairs: the select, unchanged |
| Region "Leaders"; caption "Leaders with their health, queue, throughput, followers and scan errors" | Gone with the table. Each card is named by its leader |
| Column "Leader" | The card's heading |
| Column "Health" | The status pill |
| Column "Queued" | "Waiting" |
| Column "Completed, last hour" | "Last hour" (read out as "Last hour, finished") |
| Column "Completed, last day" | "Last day" (read out as "Last day, finished") |
| Column "Failed attempts, last day" | "Failed" (read out as "Failed tries, last day") |
| Column "Active followers by pool": "default 2 · gpu 1" | Footer: "{n} followers · default 2, gpu 1" / "1 follower · …" / "No followers at work" |
| Column "Oldest queued job (since created)": "{duration}" / "Nothing queued" | Footer: "oldest waiting {duration}" / "nothing waiting" |
| Column "Last scan errors": "{location}: {error}" / "None" | "Needs a look": "{leader} could not scan {location}. {error}" / nothing |
| Column "Throughput, 24 hours" | The chart, named in words |
| "Figures as of {time}" | "The last figures are from {time}: {n} waiting, {n} followers." |
| "No successful poll yet" | "Figures appear after the first check that works." |
| "Last error: {error}" | Same |
| List name "Labels" | Same |
| (none) | Region "Totals": "Waiting now", "Finished, last hour", "Finished, last day", "Followers at work" |
| (none) | "These include the last figures from {leaders}, which the console cannot check right now." |
| (none) | "Needs a look" |
| (none) | "{leader} revoked this console's credential. A console administrator must replace it." |
| (none) | "{n} tries failed in the last day. {n} on {leader}, {n} on {leader}." / "1 try failed in the last day. 1 on {leader}." |
| (none) | Not answering: "No answer since {time}, after {n in words} tries." / "No answer yet, after {n in words} tries." |
| (none) | "Recordings already claimed keep going; this console just cannot see them." |
| (none) | Revoked: "This leader revoked the console's credential. A console administrator must replace it." and "The leader itself keeps working; this console just cannot see it." |
| (none) | Switched off: "This leader is switched off in the console, so nothing is asked of it." |
| (none) | Not yet checked: "The first check has not come back yet." |
| (none) | "The console has no figures for this leader." (a health the console does not know) |
| (none) | "See what {leader} last reported" |
| Chart: "Jobs completed per hour over the last 24 hours: latest {n}, highest {n}." | "Finished per hour over the last 24 hours: latest {n}, highest {n}." ("latest unknown" kept) |
| Chart: "Unreachable in {n} five-minute period(s)." | "No answer in {n} five-minute period(s)." |
| Chart: "No throughput history yet." | "No history yet." |
| "History unavailable" | "No history to show" |
| "Loading…" (chart) | Same |

#### Sign-in (`pages/SignInPage.tsx`)

| Was | Is |
| --- | --- |
| "Sign in to the SwarmScribe console" | "Sign in" |
| (none) | "Every leader you look after, in one place." |
| (none) | "See what is waiting, what finished and what needs a look. Everything you change here is recorded under your name, on the leader as well as here." |
| (none) | `{this console's host}` |
| (none) | "Use the account your organisation gave you." |
| "You have signed out." | "You are signed out." |
| "You are already signed in as {who}. Continue to the console." | "You are already signed in as {who}. Go to the console." |
| "Loading sign-in options…" | "Loading the ways to sign in…" |
| "No sign-in provider is configured." | "No way to sign in is set up on this console. Tell whoever runs it." |
| "Sign in with Microsoft Entra ID" | "Continue with Microsoft" |
| "Sign in with Google" | "Continue with Google" |
| "Sign in with {provider}" | "Continue with {provider}" |
| (none) | "The console signs you out after a spell with no activity, and after a longer fixed time whatever you are doing." |

#### Not found (`pages/NotFoundPage.tsx`)

| Was | Is |
| --- | --- |
| "Page not found" | Same |
| "There is no console page at this address. Go to the fleet overview." | Same |

#### Shared pieces (`components/*`, `pages/leader/common.tsx`)

| Was | Is |
| --- | --- |
| "Try again" (an error panel's retry) | Same |
| "Reload" | Same |
| "Refresh" | Same |
| (none) | "Loaded at {time}" (beside Refresh) |
| "Loading {jobs, followers, locations, join tokens, the consent report, leaders, console administrators}…" | Same |
| "Loading grants…" | "Loading the roles…" |
| "needs {role}" (beside a switched-off button) | Same |
| "Close" (a confirmation's safe choice) | "No, go back" |
| "Working…" | Same |
| "–" (no value) | Same |

#### A leader's page (`pages/leader/LeaderPage.tsx`, `pages/leader/tabs.ts`)

| Was | Is |
| --- | --- |
| "Fleet" (breadcrumb) | Same |
| "Your role on {leader}: {role}. Actions that need a higher role are shown disabled." | Viewer: "You are a viewer here. What needs an operator or an admin is shown, but switched off." Operator: "You are an operator here. What needs an admin is shown, but switched off." Admin: "You are an admin here. Nothing here is switched off for you." |
| "The console cannot reach {leader}; reads and actions will fail until it answers. The last successful poll was at {time}." | "{leader} is not answering, so nothing here can be read or changed until it does. It last answered at {time}." |
| "{leader} revoked the console's credential. A console administrator must replace it. The last successful poll was at {time}." | "{leader} revoked the console's credential. A console administrator must replace it. It last answered at {time}." |
| "{leader} is disabled in the console; reads and actions are refused. The last successful poll was at {time}." | "{leader} is switched off in the console, so nothing is asked of it. It last answered at {time}." |
| "{leader} has not answered a poll yet." | "{leader} has not answered a check yet." |
| "Health: {health}." | Same |
| "Loading…" | Same |
| "This leader is not visible to you: it is not registered, or you hold no role on it. Back to the fleet." | "You cannot see this leader: the console does not know it, or you have no role on it. Back to the fleet." |
| Navigation name "{leader} sections" | Same |
| "Pools and followers", "Jobs", "Locations", "Join tokens", "Consent report" | Same |
| (none) | List name "Labels" |

#### Jobs (`pages/leader/JobsTab.tsx`)

| Was | Is |
| --- | --- |
| Select "State": "All states", "queued", "leased", "completed", "failed", "cancelled" | Pills in a group named "Show jobs that are": "All", "Waiting", "Being worked on", "Failed", "Finished", "Cancelled", each followed by its count when known. A state in the address that has no pill is shown as its own pressed pill, as sent |
| "Location", "All locations" | Same |
| Columns "Job", "State", "Recording", "Pool", "Priority", "Actions" | Same |
| Column "Attempts" | "Tries" |
| Column "Created" | "Queued" |
| Column "Detail" | Gone: its text is the note under Recording |
| State cell "queued" | "Waiting" |
| State cell "leased" with "Leased by {id}" in Detail | "With follower {id}"; "Being worked on" when the leader names no follower |
| State cell "completed" | "Finished" |
| State cell "failed" | "Failed" |
| State cell "cancelled" | "Cancelled" |
| Recording "{location}: {key}" | "{key}", and beneath it "From {location}" |
| Detail "{failure reason}" | "From {location} · {failure reason}" |
| Detail "Cancelled by {who}" | "From {location} · Cancelled by {who}" |
| Detail "No speech found" | "From {location} · No speech found" |
| "{n} of {m}" | Same |
| "Retry"; named "Retry job {id}" | "Try again"; named "Try again: job {id}" |
| "Priority"; named "Priority of job {id}" | Same |
| "Cancel"; named "Cancel job {id}" | Same |
| "Job {id} is queued again." | "Job {id} is waiting again." |
| "Job {id} is cancelled." | Same |
| "Priority of job {id} is set." | Same |
| "No jobs match this filter." | "No jobs match." |
| "This leader has no jobs." | Same |
| "Showing the newest 100 jobs. Filter to narrow them." | Same |
| Region "Job list" | Same |
| "Cancel job {id}?" | Same |
| "The job for {key} stops and stays stopped unless someone retries it." | "The job for {key} stops, and stays stopped unless someone tries it again." |
| "Cancel job" (confirm) | Same |
| "Priority of job {id}" (dialog); "Priority"; "A whole number from -1000 to 1000; higher runs first."; "Enter a whole number from -1000 to 1000."; "Cancel"; "Set priority" | Same |

#### Pools and followers (`pages/leader/PoolsTab.tsx`)

| Was | Is |
| --- | --- |
| "Pools", "Followers" (headings and region names) | Same |
| "No successful poll yet, so pool figures are not known." | "No check has worked yet, so the pools are not known." |
| "From the poll at {time}." | "From the check at {time}." |
| Pools columns "Pool", "Revoked", "Gone" | Same |
| "Queued" | "Waiting" |
| "Leased" | "Being worked on" |
| "Active followers" | "Followers at work" |
| "Draining" | "Winding down" |
| "No followers have joined this leader." | "No follower has joined this leader yet." |
| Followers columns "Follower", "Pool", "State", "Device", "Last seen", "Actions" | Same |
| Followers column "Device": "cuda" / "cpu" (the follower's own code) | "GPU (CUDA)" / "CPU"; "–" if it reported none; a device the console does not know is shown as sent |
| "Leases" | "Working on" |
| State "active" / "draining" / "revoked" / "gone" | "At work" / "Winding down" / "Revoked" / "Gone" |
| "Drain"; named "Drain follower {id}" | "Wind down"; named "Wind down follower {id}" |
| "Revoke"; named "Revoke follower {id}" | Same |
| "Follower {id} is draining." | "Follower {id} is winding down: it finishes what it has and takes nothing new." |
| "Revoke follower {id}?" | Same |
| "The follower can no longer take work and its leased jobs go back to the queue. It needs a new join token to come back." | "The follower can take no more work, and the jobs it holds go back to waiting. It needs a new join token to come back." |
| "Revoke follower" (confirm) | Same |
| "Follower {id} is revoked; {n} leased jobs went back to the queue." | "Follower {id} is revoked. {n} jobs went back to waiting." / "… 1 job went back to waiting." |

#### Locations (`pages/leader/LocationsTab.tsx`, `pages/leader/locationForm.ts`)

| Was | Is |
| --- | --- |
| "Add location" (button; dialog submit) | Same |
| "This leader has no locations." | "This leader has no locations yet." |
| Region "Location list" | Same |
| Columns "Location", "Folder", "Pool", "Last scan", "Actions" | Same |
| Column "Device": "any" / "cuda" / "cpu" | A note under Pool: "Any device" / "GPU (CUDA) only" / "CPU only" |
| Column "Channels": "mono" / "stereo_split (A, B)" / "auto (A, B)" | A note under Folder: "Mono" / "Stereo, one speaker per side: A, B" / "Automatic: A, B" |
| Column "Scan every": "{duration}" | A note under Scanning: "Every {duration}" |
| Column "Enabled": "Yes" / "No" | Column "Scanning": "On" / "Switched off" |
| "Input prefix {prefix}" | "Looks in {prefix}" |
| "Never" (last scan) | "Not yet" |
| "Scan requested" | "A scan is asked for" |
| "{the leader's scan error}" | Same |
| "Scan now"; named "Scan now {location}" | Same |
| "Disable"; named "Disable {location}" | "Switch off"; named "Switch off {location}" |
| "Enable"; named "Enable {location}" | "Switch on"; named "Switch on {location}" |
| "A scan of {location} is requested." | "A scan of {location} is asked for." |
| "{location} is enabled." | "{location} is switched on." |
| "{location} is disabled." | "{location} is switched off." |
| "Location {name} is added." | Same |
| "Disable {location}?" | "Switch off {location}?" |
| "The leader stops scanning this location for new recordings until it is enabled again. Jobs already made are not affected." | "The leader stops looking in this location for new recordings until it is switched on again. Jobs already made carry on." |
| "Disable location" (confirm) | "Switch it off" |
| "Add a location to {leader}" | Same |
| Field labels "Name", "Folder on the leader (absolute path)", "Input prefix", "Output prefix", "Pool", "Required device", "Scan interval in seconds", "Channels", "Left channel label", "Right channel label" | Same |
| The six help texts under those fields | Same |
| Device options "Any (default)", "CUDA GPU", "CPU" | "Any device (the default)", "GPU (CUDA) only", "CPU only" |
| Channel options "Mono", "Stereo, one speaker per channel", "Automatic" | "Mono", "Stereo, one speaker per side", "Automatic" |
| "The values were not accepted. Check each field and try again." | Same |
| The form's nine field messages (name, folder, prefix, interval, label length, space, control characters, labels must differ) | Same |
| "Cancel" | Same |

#### Join tokens (`pages/leader/TokensTab.tsx`)

| Was | Is |
| --- | --- |
| "Join tokens need the admin role on {leader}. Your role is {role}." | Same |
| "Create join token" | Same |
| "No join tokens." | "No join tokens yet." |
| Region "Join token list" | Same |
| Columns "Token", "Pool", "State", "Expires", "Actions" | Same |
| Column "Uses" | "Used" |
| Column "Created by" | "Made by" |
| State "Usable" | "Can be used" |
| States "Revoked", "Expired", "Used up" | Same |
| "{n} of {m}" | Same |
| "Revoke"; named "Revoke token {id}" | Same |
| "Join token {id} is created."; "Join token {id} is revoked." | Same |
| "Revoke join token {id}?" | Same |
| "No new follower can join with it. Followers that already joined are not affected." | "No new follower can join with it. Followers that already joined carry on." |
| "Revoke token" (confirm) | Same |
| "Create a join token for {leader}"; "Pool"; "Expires after (days, 1 to 90)"; "Uses (1 to 10000)"; the three field messages; "Cancel"; "Create token" | Same |
| Dialog "Join token created" | "Here is the join token. It is shown once." |
| "This is the only time the token is shown. Copy it now and give it to whoever starts the follower. Pool {pool}; up to {n} use(s); expires {time}." | "Copy it now and give it to the machine that will join the {pool} pool. After you close this, nobody can read it again, including you." |
| "Join token" (field) | Same |
| "Copy token" | "Copy" |
| (nothing shown before copying) | "Not copied yet." |
| "Copied to the clipboard." | "Copied." |
| "Copying failed: select the token and copy it." | "Copying did not work. Select the token and copy it yourself." |
| "The token is not shown again. To close without copying it, press Escape again or choose I have stored it again." | Same |
| (none) | Facts: "Pool" {pool}; "Can be used" "once" / "{n} times"; "Expires" {time} |
| "I have stored it" | Same |

#### Consent report (`pages/leader/ConsentTab.tsx`)

| Was | Is |
| --- | --- |
| "Recordings by consent state, and transcripts made from recordings that are no longer consented." | "How many recordings are consented, and the transcripts that were made from a recording no longer consented." |
| "The report is cut short; the leader holds more flagged transcripts." | "This list is cut short: the leader holds more transcripts to review than it sent." |
| "By location"; region "Consent by location"; columns "Location", "Consented", "Not consented", "Withdrawn", "Missing" | Same |
| "This leader has no locations." | "This leader has no locations yet." |
| "Transcripts to review" (heading and region) | Same |
| "No transcript was made from a recording that is no longer consented." | Same |
| Columns "Job", "Recording" | Same |
| Column "Completed" | "Finished" |
| Column "Outputs" | "Files written" |
| "{location}: {key}"; "{output location}: {file}" | Same |

#### Administration (`pages/admin/*`)

| Was | Is |
| --- | --- |
| Page headings "Leaders", "Grants", "Console administrators" | One page heading, "Administration"; the section is an `h2` |
| (none) | "Console administrators decide which leaders this console talks to and who may use them. Being one gives you no role on any leader by itself." |
| Navigation name "Administration"; links "Leaders", "Console administrators" | Same |
| Link "Grants" | "Who can do what" |
| "Console administration needs a console administrator. Ask one to add you." | "Administration is for console administrators. Ask one to add you." |
| **Leaders** | |
| (none) | Heading: "Leaders" while loading, then "1 leader" / "{n} leaders" |
| "Add leader" (button) | "Add a leader" |
| "No leaders are registered." | "This console talks to no leaders yet." |
| Region "Registered leaders"; columns "Leader", "Address", "Labels", "Credential", "Actions" | Same |
| Column "Enabled": "Yes" / "No" | Column "State": "On" / "Switched off" |
| "Revoked by the leader"; "Set {time} by {who}" | Same |
| "Edit"; named "Edit {leader}" | Same |
| "Rotate credential"; named "Rotate credential for {leader}" | "Replace credential"; named "Replace credential for {leader}" |
| "Remove"; named "Remove {leader}" | Same |
| "Add a leader" (dialog) | Same |
| "Name", "Address (https://)", "Labels", "Console credential", "Console credential for the new address", "New console credential"; the labels and credential help texts | Same |
| Checkbox "Enabled" | "Switched on" |
| "Add leader" (dialog submit) | "Add this leader" |
| "Edit {leader}"; "Save"; "Cancel" | Same |
| "Rotate the credential for {leader}" | "Replace the credential for {leader}" |
| "Create a new console credential on the leader first, replace it here, then revoke the old one on the leader." | "Make a new console credential on the leader first, paste it here, then revoke the old one on the leader." |
| "Replace credential" (dialog submit) | Same |
| "Letters, digits, . _ - ; starts with a letter or digit; at most 100." | Same |
| "A leader URL is an https:// URL." | "A leader's address starts with https://." |
| "Leader {name} is added."; "Leader {name} is saved."; "The credential for {name} is replaced."; "Leader {name} is removed." | Same |
| "Remove {leader}?"; "Remove leader" | Same |
| "The console forgets this leader, its history and the grants that name it. The leader itself is not changed; revoke the console's credential there too." | "The console forgets this leader, its history and the roles given on it by name. The leader itself is not changed: revoke the console's credential there too." |
| (none) | Callout: "Adding a leader takes two steps" / "On the leader, an admin runs `swarmscribe-admin console create` and copies the credential it prints." / "Here, choose **Add a leader** and paste the address and that credential. The credential is never shown again." |
| **Who can do what** | |
| (none) | Heading "Who can do what" |
| "A person's role on a leader is the highest grant whose scope matches it. Adding or removing a grant applies at once. A person's group membership is read at sign-in, so a change to it applies at their next sign-in." | "A person's role on a leader is the highest one given to them that covers it. Giving or removing a role takes effect at once. Group membership is read when a person signs in, so a change to a group shows the next time they do." |
| "No grants." | "Nobody has been given a role yet." |
| Region "Grants" | Same |
| Columns "Principal", "Role", "Scope", "Added", "Actions" | "Who", "Role", "On which leaders", "Given", "Actions" |
| "{kind}:{principal}"; "{time} by {who}"; "viewer", "operator", "admin" | Same |
| "Remove"; named "Remove grant: {role} on {scope} for {kind}:{principal}" | "Remove"; named "Remove {role} on {scope} from {kind}:{principal}" |
| Form "Add a grant" | "Give a role" |
| "Role" | Same |
| "Scope" | "On which leaders" |
| "all, leader:&lt;name&gt; or label:&lt;key&gt;=&lt;value&gt;" | "Write all, leader:&lt;name&gt; or label:&lt;key&gt;=&lt;value&gt;." |
| "Add grant" | "Give the role" |
| "Grant added: {role} on {scope} for {kind}:{principal}." | "{kind}:{principal} is now {role} on {scope}." |
| "Remove this grant?" | "Remove this role?" |
| "{kind}:{principal} loses {role} on {scope} at once. Group membership is read at sign-in." | "{kind}:{principal} stops being {role} on {scope} at once." |
| "Remove grant" (confirm) | "Remove the role" |
| "Grant removed." | "The role is removed." |
| **Console administrators** | |
| "Console administrators manage leaders and grants. That gives them no role on any leader unless a grant does." | Gone: the page's opening paragraph says it |
| (none) | Heading "Console administrators" |
| Region "Console administrators" | Same |
| Column "Principal" | "Who" |
| Columns "Added", "Actions" | Same |
| "Remove"; named "Remove console administrator {kind}:{principal}" | Same |
| Form "Add a console administrator"; "Add administrator" | Same |
| "{kind}:{principal} is a console administrator." | "{kind}:{principal} is now a console administrator." |
| "Remove this console administrator?"; "Remove administrator" | Same |
| "{kind}:{principal} can no longer manage leaders and grants. The last administrator cannot be removed." | "{kind}:{principal} can no longer add leaders or give roles. The last administrator cannot be removed." |
| "Console administrator removed." | "The console administrator is removed." |
| **Naming someone** (`PrincipalFields.tsx`) | |
| "Principal kind" | "Who" |
| "Entra ID group" | "An Entra ID group" |
| "Google group" | "A Google group" |
| "Email address" (option) | "One person, by email" |
| "Domain" (option) | "Everyone at a domain" |
| "Principal" | Named for the choice: "Group object ID", "Group address", "Email address", "Domain" |
| "the group's object ID (a GUID)" | "The group's object ID in Entra ID, a GUID." |
| "the group's email address" | "The group's email address." |
| "a Google account's address" | "The address of a Google account." |
| "a Google Workspace domain, such as example.org" | "A Google Workspace domain, such as example.org." |

#### Error titles (`api/errors.ts`)

The title is ours; the server's own text follows it unchanged. Each title keeps its
meaning. "Try again in {n} seconds." is unchanged, but it is said once: when the server's own text already ends "; try again" and a retry time is known, that tail gives way to "Try again in {n} seconds."

| Code | Was | Is |
| --- | --- | --- |
| `unauthenticated` | "Your session has ended. Sign in again." | "You are signed out. Sign in again." |
| `csrf_failed` | "This page is out of date. Reload the console, then try again." | "This page is out of date. Reload it, then try again." |
| `forbidden` | "Your role does not allow this." | Same |
| `actor_not_representable` | "Your identity cannot be passed to the leader, so the console will not act for you." | "The leader cannot be told who you are, so the console will not act for you." |
| `invalid_request` | "The request was not accepted as sent." | "The console did not accept that as it was sent." |
| `too_large` | "The request is too large (over 64 KiB)." | "That is too much to send at once (over 64 KiB)." |
| `unavailable` | "The console is temporarily unavailable. Try again shortly." | "The console is not answering just now. Try again shortly." |
| `internal`, a bad path, anything unknown | "The console hit an unexpected error." | "Something went wrong in the console." |
| `method_not_allowed` | "The console does not accept that request." | "The console does not take that kind of request." |
| `not_found` | "Not found." | "There is nothing there." |
| `conflict` | "That conflicts with the current state." | "That no longer fits how things stand. Refresh, then look again." |
| `bad_request` | "The request was refused." | "The console refused that." |
| unreadable answer | "The console's answer could not be read. Reload the console, then try again." | "The console's answer could not be read. Reload the page, then try again." |
| network failure | "The console could not be reached. Check your connection." | Same |
| `leader_not_found` | "This leader is not visible to you." | "You cannot see this leader." |
| `leader_unreachable` | "The leader cannot be reached right now." | "The leader is not answering right now." |
| `leader_credential_revoked` | "The leader revoked the console's credential. A console administrator must replace it." | Same |
| `leader_credential_unreadable` | "The console cannot open its stored credential for this leader. A console administrator must replace it." | "The console cannot open the credential it holds for this leader. A console administrator must replace it." |
| `leader_credential_rejected` | "The leader does not accept the console's credential." | Same |
| `bad_gateway` | "The leader's answer could not be used. If this was an action, check whether it happened before repeating it." | "The leader's answer could not be used. If you were changing something, check whether it happened before you try again." |
| `leader_disabled` | "This leader is disabled in the console." | "This leader is switched off in the console." |
| `exists` | "That already exists." | Same |
| `last_admin` | "The last console administrator cannot be removed." | Same |
| `invalid_scope` | "That scope is not valid." | "That is not a way to say which leaders." |
| `invalid_labels` | "Those labels are not valid." | Same |
| `invalid_credential` | "That is not a console credential." | Same |
| `invalid_name` | "That name is not valid." | Same |
| `invalid_url` | "That leader URL is not allowed." | "The console may not call that address." |
| `invalid_principal` | "That principal is not valid." | "That is not a group, an address or a domain the console can use." |
| `credential_required` | "A new URL needs the credential for that URL too." | "A new address needs the credential for that address too." |
| `use_rotate` | "Replace a credential with Rotate credential." | "To change only the credential, use Replace credential." |
| `unknown_provider` | "That sign-in provider is not offered." | "That way of signing in is not offered here." |
| `not_retryable` | "Only failed or cancelled jobs can be retried." | "Only failed or cancelled jobs can be tried again." |
| `not_open` | "Only queued or leased jobs can be changed." | "Only jobs that are waiting or being worked on can be changed." |
| `already_open` | "The recording already has a queued or leased job." | "This recording already has a job waiting or being worked on." |
| `already_completed` | "This version of the recording was already transcribed." | "This version of the recording already has a transcript." |
| `not_consented` | "The recording is not consented or is no longer present." | "The recording is not consented, or is no longer there." |
| `recording_changed` | "The recording changed since the job was made." | "The recording changed after the job was made." |
| `disabled` | "The location is disabled. Enable it first." | "The location is switched off. Switch it on first." |
| `overlaps` | "That location overlaps another location." | Same |
| `root_unavailable` | "The leader cannot use that folder." | Same |
| `invalid_root` | "That folder is not valid on the leader." | Same |
| `rate_limited` | "Too many requests. Wait, then try again." | "Too many requests. Wait a little, then try again." |
| any 5xx without a known code | "The leader or the console failed to answer." | "The leader or the console did not answer." |
| a cancelled request | "The request was cancelled." | "That request was stopped." |

The texts made in `api/client.ts` for a request that never got an answer ("The console
could not be reached.", "The console's answer could not be read.", "The request path is
not a console route.") are unchanged.

## 7. Accessibility

Everything C3 established stays, and is tested where it was tested.

1. **Skip link.** First focusable element, "Skip to main content", to `#main`. Amber with
   ink text in both themes.
2. **Focus rings.** Every focusable element shows a 3px `--focus` ring, 2px off, on
   keyboard focus. The ring is 3:1 or better against the surface it is on.
3. **Focus after navigation.** Going to another page moves focus to the new page's `h1`.
   Switching between a leader's tabs, or between the sections of Administration, leaves
   focus on the link that was activated. The `h1` of the fleet overview is the same
   element while loading and after, so focus put on it survives the fleet arriving.
4. **Dialogs.** Native `<dialog>` with `showModal()`: the page behind is inert, Tab stays
   inside, Escape cancels, focus returns to the opener (or to `main` if the opener is
   gone). A confirmation focuses its safe choice. The one-time token dialog cannot be
   closed by accident, as before. The page does not scroll while a dialog is open.
5. **Focus after a row action.** Unchanged (`pages/leader/rowFocus.tsx`).
6. **No word is broken across lines** in any table cell or card, except text that opts in
   (`.long`). Checked at 768px.
7. **No sideways page scroll** at 768px on any page, nor at 390px on the fleet overview and
   sign-in. A wide table scrolls inside its own region.
8. **Actions stay in reach.** The Actions column is pinned to the right edge of its
   scrolling region at every width, and at 768px it never covers the whole table. This is
   the C3 follow-up, done here.
9. **Zero axe violations** (WCAG 2.1 A and AA) on every page and every dialog, in both
   themes, at desktop width; and at 768px for the fleet overview (menu closed and open) and
   sign-in.
10. **Colour is never the only signal.** Health is a shape and words. A job's state is
    words. A switched-off button has a dashed edge and says which role it needs. An error
    field has a thicker border, `aria-invalid` and a message. A chart's gaps are marked by
    blocks on the baseline.
11. **Cards.** The fleet overview has no table (see ruling R5). Each leader is an
    `<article>` named by its `h2`; its figures are a `<dl>`, so each number is read with
    its label; its labels are a named list; its chart is an image with a text alternative
    that says the latest and highest figures and how long the leader gave no answer. The
    count of leaders is in a polite live region. The time of the last check is not.
12. **Names contain the visible label.** A button's accessible name always contains its
    visible text, in order (WCAG 2.5.3).
13. **Targets.** Controls are 44px tall; a button inside a table row is 36px.
14. **Reduced motion.** There is no animation. `prefers-reduced-motion` turns off any
    transition a browser adds.
15. **High contrast.** In `forced-colors` mode the hexagon mark keeps a visible shape.
16. **Live regions.** Results of actions are `role="status"`; errors are `role="alert"`.
    Times ("Checked at", "Loaded at") are outside any live region.
17. **Long values.** A leader's name can be 100 characters and a label's value 255, with
    nothing to break on. They wrap inside the card, the rail, the pill and the page header.
    The page does not scroll sideways at 1280, 768 or 390 pixels because of one.
18. **The chosen theme wins.** Dark chosen on a light system, or Light on a dark one, is
    what is painted, and it survives a reload. The rail is ink either way.

## 8. What does not change

- Routes, and `app/routePrefixes.json`.
- Every API call, its method, path, body and timing, with one reduction: a leader that is
  not answering no longer has its 24-hour history fetched for the overview, because its
  card shows no chart (ruling M4).
- Roles and what each may do; the allow-list in `api/roles.ts`.
- Security: no inline style, no HTML injection, nothing in storage but the theme, a join
  token or credential never in a URL, in storage or in state that outlives its dialog.
  `eslint.config.js` and `scripts/check-dist.mjs` are not touched.
- Polling: intervals, the idle pause, the hidden-tab pause, the 401 latch.
- Form validation rules and what each form sends.
- The dialog component's behaviour, the double-submit guard, the one-time token rules.
- The theme switch's three choices and where the choice is stored.
- Dependencies: none added, none removed.
- The backend. Nothing outside `packages/console-web` changes.

## 9. Rulings

### From the controller (the owner can overturn any)

| # | Ruling |
| --- | --- |
| C1 | Left rail with the logo and wordmark, Fleet, each visible leader beneath it, Administration; the person and sign-out at the bottom. A top bar with a menu button at narrow widths. |
| C2 | Tables and cards on ink-2 panels. Paper with the amber offset shadow only for dialogs, callouts and the sign-in card. |
| C3 | Dark Ink is the default. A light theme is kept and designed: paper ground, ink rail. Both pass AA. |
| C4 | The copy is rewritten in the brand voice; the fixed error titles keep their meaning. |
| C5 | Fleet overview: a card per leader with a wide chart, totals above, a "Needs a look" paper card, label filter pills. A table only if accessibility requires one. |
| C6 | No change to behaviour, routes, API calls, roles or security. No test deleted or weakened. |
| C7 | Everything C3 established for accessibility stays, plus a pinned or always-reachable Actions column at tablet width. |

### Made while writing this spec

| # | Ruling | Why |
| --- | --- | --- |
| M1 | **No table on the fleet overview.** Cards only. | A table's one advantage is moving down a column with a screen reader's table keys. The totals row gives the fleet-wide figures, and each card pairs every number with its label in a `<dl>`. The table was 72rem wide and scrolled sideways on every tablet; cards reflow. Section 7.11 lists what keeps the cards accessible. |
| M2 | **"Needs a look" comes first in the grid**, not last as drawn. | Drawn with three leaders it sat at the bottom right. With twelve it would be below the fold, which defeats it. First in the grid keeps the drawing's composition. Moving it back is one line. |
| M3 | **Sheets invert in the light theme**: ink sheets on a paper page. | Paper on paper has no edge. PaperDesk drew its callout this way. The amber offset shadow is the same in both. |
| M4 | **A leader that is not answering has no chart**, so its history is not fetched. | As drawn. It is the one change to what is requested, and it is a reduction. |
| M5 | **The sign-in page's `h1` is the card's "Sign in"**; the big serif line is a paragraph. | The drawing made the slogan the `h1` and "Sign in" an `h2`. A page's first heading should say what the page is. It looks the same. |
| M6 | **The sign-in brand panel is ink in both themes.** | The amber "Scribe" is 1.5:1 on paper. On ink it is the brand, and it reads as the rail the person is about to see. |
| M7 | **Label pills up to six pairs, then the old select.** | Pills for thirty label pairs would push the fleet off the screen. |
| M8 | **Job state pills carry counts from the last check**, and no counts while a location filter is on. | The counts are per leader, not per location, and can be a few seconds behind the list. Showing them beside a location filter would be wrong. |
| M9 | **A "Cancelled" pill is added, and the Priority column is kept.** | The drawing has neither. The filter offered cancelled before, and a person who sets a priority must be able to see it. |
| M10 | **Jobs: the Detail column folds into a note under Recording. Locations: nine columns become six**, with device, channels and scan interval as notes. | At 1280px with the rail, nine columns do not fit and the pinned Actions column covered the last ones. No information is removed. |
| M11 | **Administration is one page.** Its three sections share one `h1`, and switching section keeps focus on the link, as a leader's tabs do. | As drawn. Without it, focus would land on the same heading, "Administration", three times over. |
| M12 | **Control edges are `--edge`, not the drawing's 30% line.** | The drawn ghost-button outline is 2.4:1 on ink. A field's edge is how a person finds the field; 3:1 is required. Slightly brighter outlines everywhere. |
| M13 | **No green.** A finished action is reported in the text colour with a hexagon. | The brand has amber, ink, paper and one red. |
| M14 | **The confirmation's safe button is "No, go back"**, not "Close". | "Close" beside "Cancel job" does not say what it will do. |
| M15 | **"Drain" becomes "Wind down".** | "Drain" is our word, not the reader's. The notice now says what it means. "Revoke" is kept: it is exact and the leader's own tools use it. |
| M16 | **Grants are "roles given".** Scope is "On which leaders"; principal is "Who". The stored forms (`all`, `label:region=eu`, `domain:example.org`) are shown as they are. | The page is "Who can do what". The stored forms are what a person types and what the leader's tools print, so hiding them would mislead. |
| M17 | **CSS is split into files under `src/styles/`** (seven in R1, an eighth in R2), imported in order by `src/styles.css`. Vite still emits one hashed stylesheet. | One 1,800-line file would be unworkable. |
| M18 | **The favicon is the brand mark**, as a hashed SVG asset. | The page had an empty `data:` icon. |
| M19 | **The Actions column is pinned at every width**, not only on tablets, in CSS alone. | Simpler, and a narrow desktop window has the same problem. |
| M20 | **Main content is at most 1400px wide.** | On a very wide monitor two cards would stretch to 900px each. |

## 10. Mockup details not built as drawn

| Drawn | Why not | Built instead |
| --- | --- | --- |
| Inline `style` attributes throughout | CSP `style-src 'self'`; lint forbids `style` props | Tokens and classes in `src/styles/` |
| "Checked at 09:36:07 · next check in 10 s" | A countdown redraws every second and, in a live region, would never stop talking | "Checked at {time}, and every 10 s". True and still. |
| "With gpu-02" as a job's state | The API gives the follower's id, not a name | "With follower 1a2b3c4d" (the first eight characters of the id) |
| "Claimed 4 minutes ago" under a recording | The API does not say when a job was claimed | The note says where the recording came from |
| "Transcript written beside the recording" under a finished job | The console does not know where transcripts were written | "From {location}", and "No speech found" when the leader says so |
| "2 attempts failed today. … Both will be tried again." | "Today" is not the last 24 hours, and the console cannot know a failed try will be retried | "2 tries failed in the last day. 1 on eu-1, 1 on us-1." |
| "eu-1 could not read a folder." | The leader sends any scan error, not only an unreadable folder | "eu-1 could not scan archive." followed by the leader's own text |
| "Admin on 2 leaders" (one line) | A person can hold different roles on different leaders | "Admin on 2, viewer on 1 leader" |
| A Health column in Administration, Leaders ("Answering, checked 6 s ago") | Health comes from the fleet, which lists only leaders the person has a role on. A console administrator has no role by default, so the column would often be empty | The columns the page had: State (on or switched off) and Credential (who set it, or revoked) |
| "You stay signed in for 8 hours, or until an hour passes without you doing anything." | Both times are configurable on the server, and the sign-in page has no session to ask | "The console signs you out after a spell with no activity, and after a longer fixed time whatever you are doing." See O3. |
| "Expires in 7 days" in the token dialog | A relative time is wrong a minute later, and this dialog stays open until dismissed | The date and time it expires |
| No theme switch anywhere | The app has one, and ruling C3 keeps it | "Theme" in the rail's footer |
| No "Cancelled" pill; no Priority column | See M9 | Both present |
| The tab navigation named "eu-1" | The same name as the page heading | "eu-1 sections", as it was |
| "Replace the credential for eu-1" as a button's name | The name must contain the visible text "Replace credential" | "Replace credential for eu-1" |
| "Try job 35306296 again" as a button's name | The same rule | "Try again: job 35306296" |
| Sub-pixel sizes such as 12.5px in a `px` unit | Sizes follow the browser's text size | The same sizes in `rem` |

Nothing in the drawings needed an inline style, a web font or an off-origin image that
could not be replaced. The hexagon is a `clip-path`; the logo is inline SVG.

## 11. Decisions that are the owner's

| # | Question | What is built meanwhile |
| --- | --- | --- |
| O1 | **"Needs a look" first or last** in the grid (M2). | First. |
| O2 | **A table view for large fleets.** Past a dozen or so leaders, cards are slower to scan than rows. A second view would be new behaviour. | Cards only. |
| O3 | **The sign-in note's exact times.** To say "8 hours" and "an hour" truthfully the console's API would have to send its session settings with the list of providers. That is a backend change. | The general sentence. |
| O4 | **"Wind down" for drain** (M15), and **"Who can do what" vocabulary** (M16). | As written in section 6. |
| O5 | **Follower names.** The drawing shows "gpu-02". Followers have no name in the API. | The id's first eight characters. |
| O6 | **Should the console be dark for everyone until they choose otherwise?** C3 says the app "already honours the system preference", so a person on a light system gets the light theme first. Browsers always report light or dark, so "dark by default" only shows on a dark system. Making Ink the first thing everyone sees means changing what "System" does, or defaulting the switch to Dark. | Follows the system. Dark only where the system is dark or Dark is chosen. |
| O7 | **Health in Administration.** It needs either a console-administrator view of the fleet in the API, or accepting an often-empty column. | Not shown. |

## 12. Delivery

Two plans. Each leaves the app working and shippable.

**R1, shell and fleet** restyles the whole app at once (every existing class gets the new
look in both themes) and rebuilds the frame, the fleet overview and sign-in. After R1 the
leader pages and Administration wear the brand but keep their old structure and words.

**R2, leader and admin** rebuilds the leader's page and tabs, the dialogs' wording, the
one-time token dialog and Administration, and finishes the copy.

Each plan's last task photographs every screen in both themes at 1280 and 768 pixels wide
(`npm run screens`) for a person to look at. Unit tests and axe pass layouts that are
plainly wrong to the eye.

### Files

New in R1: `src/styles/{tokens,base,controls,surfaces,shell,fleet,signin}.css`,
`src/styles/tokens.test.ts`, `src/components/Brand.tsx`, `src/assets/favicon.svg`,
`src/pages/SignInPage.test.tsx` (the sign-in tests, moved out of `src/shell.test.tsx`),
`e2e/tests/theme.spec.ts`, `e2e/screens/capture.spec.ts`, `playwright.screens.config.ts`.

New in R2: `src/styles/detail.css`, `src/pages/admin/AdminPage.tsx`.

`src/styles.css` becomes the list of imports. Everything else is edited in place.
