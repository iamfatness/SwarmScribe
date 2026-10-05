import { Fragment } from "react";

/**
 * A name and the slash that follows it, or a last name with none. An address's scheme and
 * host ("https://eu-1.leaders.example/") are one piece: "https://" is never left alone at the
 * end of a line.
 */
const PIECE = /^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^/\\]*[/\\]?|[^/\\]*[/\\]|[^/\\]+/g;

const HOST = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//;
/** A scheme and host up to this long stay on one line; a longer one may break, as a name does. */
const HOST_KEPT_WHOLE = 40;

/**
 * A path or folder name that wraps between folders, not inside them: "incoming/" then
 * "meeting-2.wav", never "meeti" then "ng-2.wav", and never at a hyphen in a name that would
 * fit on a line, and an address never after "https://". Each name and its slash is kept whole
 * (styles/surfaces.css, .path-part), with a <wbr> after every slash as the place to break. A
 * single name wider than its cell still breaks, as the last resort, so a cell can never push
 * the page sideways. An address's scheme and host of ordinary length never break at all
 * (.path-host): the column makes room for them. The text itself is unchanged: copying it,
 * searching for it and a screen reader all see the path as it is.
 */
export function BreakPath({ text }: { text: string }) {
  const pieces = text.match(PIECE) ?? [];
  const hasHost = HOST.test(text);
  return (
    <>
      {pieces.map((piece, index) => (
        <Fragment key={index}>
          <span
            className={
              index === 0 && hasHost && piece.length <= HOST_KEPT_WHOLE ? "path-part path-host" : "path-part"
            }
          >
            {piece}
          </span>
          {index < pieces.length - 1 && <wbr />}
        </Fragment>
      ))}
    </>
  );
}
