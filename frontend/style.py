"""The PersonaOS look ("Aura") on top of the Streamlit theme in .streamlit/config.toml.

The theme sets colours, fonts, radii and pill buttons. This adds what the theme can't:
the aura behind each page, serif hero headlines with an italic accent, mono tag pills,
chat bubbles, card styling (st.container(key="card-…") gets the class "st-key-card-…"),
a coral accent, the motion layer (hover lifts, press feedback, focus rings, entrance
fades — all disabled for people who turn motion off) and the phone layout. Only
Streamlit's stable hooks are targeted: data-testid attributes and st-key-* classes.
"""

import html

import streamlit as st

INK = "#1c1b22"
ACCENT = "#ff5b35"

_CSS = """
<style>
:root {
  --paper: #f6f3ee;
  --ink: #1c1b22;
  --ink-2: #55525f;
  --muted: #8b8795;
  --line: #e4dfd6;
  --card: #ffffff;
  --aura-blue: rgba(110, 150, 255, 0.42);
  --aura-violet: rgba(180, 160, 255, 0.40);
  --aura-pink: rgba(255, 182, 214, 0.38);
  --accent: #ff5b35;
  --bubble: #dfe8ff;
  --shadow: 0 1px 2px rgba(28, 27, 34, 0.04), 0 8px 24px rgba(28, 27, 34, 0.06);
  --shadow-hover: 0 2px 4px rgba(28, 27, 34, 0.05), 0 14px 34px rgba(28, 27, 34, 0.10);
  --ease: cubic-bezier(0.2, 0.7, 0.3, 1);
}
html { scroll-behavior: smooth; }

/* Page: warm paper with a soft aura at the top. The aura scrolls with the page on
   phones (fixed backgrounds repaint on every scroll frame there) and stays put on
   larger screens. */
[data-testid="stApp"] {
  background:
    radial-gradient(52rem 24rem at 50% -7rem, var(--aura-blue), transparent 70%),
    radial-gradient(34rem 20rem at 14% -3rem, var(--aura-violet), transparent 72%),
    radial-gradient(34rem 20rem at 88% -2rem, var(--aura-pink), transparent 72%),
    var(--paper);
}
@media (min-width: 900px) {
  [data-testid="stApp"] { background-attachment: fixed; }
}
/* The fixed header frosts over whatever scrolls beneath it, so the logo and nav stay
   readable on a scrolled page (chat opens scrolled to the newest message). The element
   names raise specificity above Streamlit's own emotion rules. */
header[data-testid="stHeader"] {
  background: rgba(246, 243, 238, 0.88);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(28, 27, 34, 0.06);
}
div[data-testid="stMainBlockContainer"] { padding-top: 4.6rem; max-width: 72rem; }

/* Scrollbars: slim and quiet. */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: #d6d1c6;
  border-radius: 999px;
  border: 2px solid transparent;
  background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background-color: #b9b4a8; }

/* Buttons: lift on hover, press down on tap. (Hover only where a pointer exists.) */
[data-testid^="stBaseButton"],
a[data-testid^="stBaseLinkButton"] {
  transition: transform 0.15s var(--ease), box-shadow 0.2s var(--ease),
    background-color 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}
@media (hover: hover) {
  [data-testid^="stBaseButton"]:hover:not(:disabled),
  a[data-testid^="stBaseLinkButton"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(28, 27, 34, 0.16);
  }
}
[data-testid^="stBaseButton"]:active:not(:disabled),
a[data-testid^="stBaseLinkButton"]:active {
  transform: translateY(0) scale(0.97);
  box-shadow: none;
}

/* Top navigation as a pill bar; the current page is an ink pill. */
[data-testid="stTopNavLink"] {
  border-radius: 999px;
  padding: 0.3rem 0.95rem;
  font-size: 0.86rem;
  color: var(--ink-2);
  transition: background-color 0.18s ease, color 0.18s ease;
}
@media (hover: hover) {
  [data-testid="stTopNavLink"]:hover:not([aria-current="page"]) {
    background: rgba(28, 27, 34, 0.07);
    color: var(--ink);
  }
}
[data-testid="stTopNavLink"][aria-current="page"] { background: var(--ink); color: #fff; }
[data-testid="stTopNavLink"][aria-current="page"] * { color: #fff; }

/* Hero: mono tag pill, serif headline with an italic accent, quiet subtitle.
   The pieces rise in softly, one after another. */
.pos-hero { position: relative; text-align: center; padding: 1.5rem 0 1.25rem; }
.pos-hero::before {
  content: "";
  position: absolute;
  inset: -2rem -1rem 0;
  background-image:
    linear-gradient(rgba(28, 27, 34, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(28, 27, 34, 0.05) 1px, transparent 1px);
  background-size: 26px 26px;
  -webkit-mask-image: radial-gradient(ellipse at 50% 30%, #000 0%, transparent 70%);
  mask-image: radial-gradient(ellipse at 50% 30%, #000 0%, transparent 70%);
  pointer-events: none;
  z-index: 0;
}
.pos-hero > * {
  position: relative;
  z-index: 1;
  animation: pos-rise 0.45s var(--ease) both;
}
.pos-hero .pos-title { animation-delay: 0.05s; }
.pos-hero .pos-sub { animation-delay: 0.1s; }
@keyframes pos-rise {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}
.pos-hero.left { text-align: left; padding-top: 0.25rem; }
.pos-hero.left::before { display: none; }
.pos-title {
  font-family: "Instrument Serif", Georgia, serif;
  font-weight: 400;
  font-size: clamp(2.4rem, 6vw, 4.75rem);
  line-height: 1.02;
  letter-spacing: -0.01em;
  color: var(--ink);
  margin: 0.65rem 0 0.6rem;
}
.pos-hero.left .pos-title { font-size: clamp(2rem, 4vw, 2.8rem); margin-top: 0.5rem; }
.pos-title em { font-style: italic; }
.pos-sub { color: var(--ink-2); font-size: 1.02rem; max-width: 36rem; margin: 0 auto; }
.pos-hero.left .pos-sub { margin: 0; }
.pos-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-family: "JetBrains Mono", monospace;
  font-size: 0.7rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-2);
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0.28rem 0.6rem;
}
.pos-tag::before { content: ""; width: 7px; height: 7px; border-radius: 2px; background: #6e96ff; }
.pos-tag.live::before { background: var(--accent); border-radius: 50%; }
.pos-stat {
  display: inline-block;
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink);
  border: 1.5px solid var(--ink);
  border-radius: 999px;
  padding: 0.25rem 0.75rem;
}

/* Floating labels on the sign-in page: drifting gently (hidden on small screens). */
.pos-floats { position: relative; height: 0; }
.pos-float {
  position: absolute;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.9rem;
  color: var(--ink);
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0.45rem 0.9rem;
  box-shadow: var(--shadow);
  white-space: nowrap;
  animation: pos-bob 7s ease-in-out infinite alternate;
}
.pos-float:nth-child(2) { animation-delay: -2s; }
.pos-float:nth-child(3) { animation-delay: -4s; }
.pos-float:nth-child(4) { animation-delay: -6s; }
@keyframes pos-bob {
  from { transform: translateY(-4px); }
  to { transform: translateY(5px); }
}
.pos-float.coral { background: #ffd9cc; border-color: #ffc3b0; }
.pos-float.blue { background: var(--bubble); border-color: #c9d7ff; }
@media (max-width: 900px) { .pos-floats { display: none; } }

/* Cards: st.container(key="card-…"), rising slightly under the pointer. */
[class*="st-key-card"] {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 1.1rem 1.2rem;
  box-shadow: var(--shadow);
  transition: box-shadow 0.25s var(--ease), transform 0.25s var(--ease);
}
@media (hover: hover) {
  [class*="st-key-card"]:hover { transform: translateY(-2px); box-shadow: var(--shadow-hover); }
}
[class*="st-key-auth"] {
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 1.4rem 1.5rem 1.2rem;
  box-shadow: var(--shadow);
  backdrop-filter: blur(6px);
}

/* Forms (upload, forget, delete everything) as white cards. */
[data-testid="stForm"] {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  box-shadow: var(--shadow);
}
[class*="st-key-auth"] [data-testid="stForm"] { background: none; border: 0; box-shadow: none; }

/* Expanders and tabs: quiet highlights instead of jumps. */
[data-testid="stExpander"] summary {
  border-radius: 12px;
  transition: background-color 0.15s ease;
}
@media (hover: hover) {
  [data-testid="stExpander"] summary:hover { background: rgba(28, 27, 34, 0.04); }
}
[data-testid="stTab"] { transition: color 0.15s ease; }
[data-testid="stTab"] p { font-size: 0.92rem; }
[data-testid="stTabs"] [aria-selected="true"] p { color: var(--ink); font-weight: 600; }

/* Dataframes: rounded like the cards. */
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }

/* Goal meter: a slim same-hue track; the fill glides to its new width. */
.pos-meter { display: flex; align-items: center; gap: 0.75rem; margin: 0.35rem 0 0.2rem; }
.pos-meter-track {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: #dbe7fb;
  overflow: hidden;
}
.pos-meter-fill {
  height: 100%;
  border-radius: 999px;
  background: #2a78d6;
  transition: width 0.6s var(--ease);
}
.pos-meter-label { font-size: 0.82rem; color: var(--ink-2); min-width: 5.5rem; text-align: right; }

/* Metrics as tiles with a small uppercase label. */
[data-testid="stMetric"] {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 1rem 1.15rem 0.9rem;
  box-shadow: var(--shadow);
  transition: box-shadow 0.25s var(--ease), transform 0.25s var(--ease);
}
@media (hover: hover) {
  [data-testid="stMetric"]:hover { transform: translateY(-2px); box-shadow: var(--shadow-hover); }
}
[data-testid="stMetricLabel"] p {
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-2);
}
[data-testid="stMetricValue"] { font-family: "Instrument Serif", Georgia, serif; }

/* Chat: user messages as blue bubbles on the right, replies as white cards. */
[data-testid="stChatMessage"] {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 0.85rem 1rem;
  box-shadow: var(--shadow);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  flex-direction: row-reverse;
  width: 86%;
  margin-left: auto;
  background: var(--bubble);
  border-color: #cdd9ff;
  box-shadow: none;
}
[data-testid="stChatMessageAvatarUser"] { background: var(--ink); }
[data-testid="stChatMessageAvatarAssistant"] {
  background: radial-gradient(circle at 30% 30%, #9fb8ff, #7b61ff 70%);
}
[data-testid="stChatInput"] {
  border-radius: 999px;
  box-shadow: var(--shadow);
  transition: box-shadow 0.2s ease;
}
[data-testid="stChatInput"]:focus-within {
  box-shadow: 0 0 0 3px rgba(255, 91, 53, 0.22), var(--shadow-hover);
}
[data-testid="stChatInputSubmitButton"] {
  background: var(--accent);
  border-radius: 999px;
  color: #fff;
  transition: transform 0.15s var(--ease), background-color 0.2s ease;
}
@media (hover: hover) {
  [data-testid="stChatInputSubmitButton"]:hover:not(:disabled) { transform: scale(1.08); }
}
[data-testid="stChatInputSubmitButton"]:active:not(:disabled) { transform: scale(0.95); }
[data-testid="stChatInputSubmitButton"]:disabled { background: #f3c4b6; }

/* Toasts (saved / created / deleted notices): small white cards, bottom right. */
[data-testid="stToast"] {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: 0 10px 30px rgba(28, 27, 34, 0.16);
}
[data-testid="stToast"], [data-testid="stToast"] * { color: var(--ink); }

/* Account menu: a pill in the top bar, right side (like the nav's call to action). */
[class*="st-key-account"] {
  position: fixed;
  top: 0.55rem;
  right: 3.4rem;
  z-index: 999991;
  width: auto !important;
}
[class*="st-key-account"] button {
  background: var(--ink);
  color: #fff;
  border-color: var(--ink);
}
[class*="st-key-account"] button * { color: #fff; }

/* Phones: tighter rhythm, same look. */
@media (max-width: 640px) {
  div[data-testid="stMainBlockContainer"] { padding: 4.4rem 1rem 5.5rem; }
  .pos-hero { padding: 0.9rem 0 0.8rem; }
  .pos-title { font-size: clamp(2.1rem, 11vw, 2.9rem); }
  .pos-hero.left .pos-title { font-size: 1.9rem; }
  .pos-sub { font-size: 0.95rem; }
  [data-testid="stTopNavLink"] { padding: 0.25rem 0.7rem; font-size: 0.82rem; }
  [class*="st-key-card"] { padding: 0.9rem 1rem; border-radius: 16px; }
  [class*="st-key-auth"] { padding: 1.1rem 1rem 0.9rem; border-radius: 18px; }
  [data-testid="stMetric"] { padding: 0.75rem 0.9rem 0.65rem; border-radius: 16px; }
  [data-testid="stChatMessage"] { padding: 0.7rem 0.8rem; border-radius: 16px; }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { width: 94%; }
  /* The account pill shrinks to the person icon, clear of the collapsed nav. */
  [class*="st-key-account"] { top: 0.5rem; right: 2.9rem; }
  [class*="st-key-account"] button [data-testid="stMarkdownContainer"] { display: none; }
  [class*="st-key-account"] button { padding-left: 0.55rem; padding-right: 0.55rem; }
}

/* People who turn animation off get none, anywhere. */
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, ::before, ::after {
    animation: none !important;
    transition: none !important;
  }
}
</style>
"""


def inject() -> None:
    """Add the stylesheet to the page (once per script run)."""
    st.html(_CSS)


def hero(tag: str, title: str, subtitle: str = "", *, left: bool = False, live: bool = False):
    """A page heading. In `title`, *word* becomes the italic accent (escaped otherwise)."""
    parts = html.escape(title).split("*")
    title_html = "".join(f"<em>{p}</em>" if i % 2 else p for i, p in enumerate(parts))
    tag_class = "pos-tag live" if live else "pos-tag"
    st.html(
        f'<div class="pos-hero{" left" if left else ""}">'
        f'<span class="{tag_class}">{html.escape(tag)}</span>'
        f'<div class="pos-title">{title_html}</div>'
        + (f'<p class="pos-sub">{html.escape(subtitle)}</p>' if subtitle else "")
        + "</div>"
    )


def stat(text: str) -> None:
    """An outline pill for a single fact, e.g. "Target: 12 Oct"."""
    st.html(f'<span class="pos-stat">{html.escape(text)}</span>')


def meter(ratio: float, label: str) -> None:
    """A slim progress meter: blue fill over a light blue track, label on the right."""
    width = max(0.0, min(1.0, ratio)) * 100
    st.html(
        '<div class="pos-meter"><div class="pos-meter-track">'
        f'<div class="pos-meter-fill" style="width:{width:.1f}%"></div></div>'
        f'<span class="pos-meter-label">{html.escape(label)}</span></div>'
    )


def floating_labels(labels: list[tuple[str, str, str]]) -> None:
    """Decorative pills around the sign-in hero: (text, css top/left/right, colour)."""
    pills = "".join(
        f'<span class="pos-float {colour}" style="{position}">{html.escape(text)}</span>'
        for text, position, colour in labels
    )
    st.html(f'<div class="pos-floats" aria-hidden="true">{pills}</div>')
