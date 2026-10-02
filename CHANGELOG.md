# Changelog

What changed in each version of stet, newest first. The version number is shown at the
bottom of the Settings panel.

## 0.2.0 (2 October 2026)

You now edit by double-clicking and save by clicking away, comments are marked on the page
and can be filtered, and a new view shows who changed each block. Nothing stet draws on
screen (comment marks, labels, highlights) is ever written into your file.

**Editing**

- Double-click where you want to change something and type, and the cursor lands where you
  clicked.
- There is no Save button: clicking away, or double-clicking into the next block, saves the
  change, and Esc undoes the change you are making.
- If a save is refused, what you typed stays on the page, marked, so nothing is lost.
- Shift + double-click edits a block's HTML directly, for links and other markup.
- No boxes or outlines follow the mouse, and editing looks like the page itself.

**Comments**

- Highlight text and a Comment button appears. Comments on the whole document are added from
  the sidebar.
- The newest comment is at the top.
- Every open comment is marked on the page: the block is tinted, the highlighted words are
  marked, and a numbered badge sits in the margin. Clicking a badge opens its comment.
- Filters show only what needs you (a proposal to approve), what is waiting on the agent, what
  is done, or everything, and the choice is remembered.
- The comment window says what is being commented on in plain words, and Submit sits apart
  from Cancel.
- Comments are numbered #1, #2 and so on.

**Who changed what**

- A *Who changed what* view labels every changed block in the margin, in blue for a person
  and in orange for an agent, for example *Codex, approved by Julia*, while the *Normal* view
  keeps the page clean.
- The labels use the name you started stet with (`--author`) and the agent's own name.

**Layout and settings**

- The comments sidebar is wider, can be resized by dragging its edge, and can be closed with
  its own button, with a tab on the right edge to bring it back.
- A Settings panel (top right) sets the text size for the comments and for the document
  separately, shows or hides the sidebar and resets its width, and keeps these settings in
  your browser only.
- The top bar is larger and names the tool. The note that the page's own scripts are paused
  now appears only on documents that have scripts.
- Simple line icons replace emoji.

**Tests**

- The browser tests grow from 69 to 122 checks, covering each change above and checking after
  each one that the file holds only your words.

## 0.1.0 (23 September 2026)

First public release: edit an HTML document in your browser, only the edited block is written
back, an agent's changes wait for your approval, and every change is recorded with who
proposed and who approved it.
