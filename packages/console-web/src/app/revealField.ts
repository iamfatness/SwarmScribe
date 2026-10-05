/**
 * Puts focus on a form field that was refused and brings what a person needs to read into
 * view: the field with its label and its message, and the dialog's title above them when
 * both fit. Focus alone scrolls nothing when the field is already in view, which left a tall
 * form scrolled to its buttons with the title and the first field's label cut off at the top.
 * The message is rendered after this is called, so the scroll waits a frame for it.
 */
export function revealField(control: HTMLElement): void {
  control.focus({ preventScroll: true });
  requestAnimationFrame(() => {
    if (!control.isConnected) return;
    const dialog = control.closest("dialog");
    // The label, the control and its message share one parent.
    const block = control.closest(".field")?.parentElement ?? control;
    if (dialog !== null) dialog.scrollTop = 0;
    if (typeof block.scrollIntoView === "function") block.scrollIntoView({ block: "nearest" });
  });
}
