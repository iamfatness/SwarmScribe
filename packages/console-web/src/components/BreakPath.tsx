import { Fragment } from "react";

/** A name and the slash that follows it, or a last name with none. */
const PIECE = /[^/\\]*[/\\]|[^/\\]+/g;

/**
 * A path or folder name that wraps between folders, not inside them: "incoming/" then
 * "meeting-2.wav", never "meeti" then "ng-2.wav", and never at a hyphen in a name that would
 * fit on a line. Each name and its slash is kept whole (styles/surfaces.css, .path-part), with
 * a <wbr> after every slash as the place to break. A single name wider than its cell still
 * breaks, as the last resort, so a cell can never push the page sideways. The text itself is
 * unchanged: copying it, searching for it and a screen reader all see the path as it is.
 */
export function BreakPath({ text }: { text: string }) {
  const pieces = text.match(PIECE) ?? [];
  return (
    <>
      {pieces.map((piece, index) => (
        <Fragment key={index}>
          <span className="path-part">{piece}</span>
          {index < pieces.length - 1 && <wbr />}
        </Fragment>
      ))}
    </>
  );
}
