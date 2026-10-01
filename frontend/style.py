"""The PersonaOS look ("Aura") on top of the Streamlit theme in .streamlit/config.toml.

The theme sets colours, fonts, radii and pill buttons. This adds what the theme can't:
the aura behind each page, serif hero headlines with an italic accent, mono tag pills,
chat bubbles, card styling (st.container(key="card-…") gets the class "st-key-card-…"),
and a coral accent. Only Streamlit's stable hooks are targeted: data-testid attributes
and st-key-* classes.
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
}

/* Page: warm paper with a soft aura at the top. */
[data-testid="stApp"] {
  background:
    radial-gradient(52rem 24rem at 50% -7rem, var(--aura-blue), transparent 70%),
    radial-gradient(34rem 20rem at 14% -3rem, var(--aura-violet), transparent 72%),
    radial-gradient(34rem 20rem at 88% -2rem, var(--aura-pink), transparent 72%),
    var(--paper);
  background-attachment: fixed;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 4.5rem; max-width: 72rem; }

/* Top navigation as a pill bar; the current page is an ink pill. */
[data-testid="stTopNavLink"] {
  border-radius: 999px;
  padding: 0.3rem 0.95rem;
  font-size: 0.86rem;
  color: var(--ink-2);
}
[data-testid="stTopNavLink"][aria-current="page"] { background: var(--ink); color: #fff; }
[data-testid="stTopNavLink"][aria-current="page"] * { color: #fff; }

/* Hero: mono tag pill, serif headline with an italic accent, quiet subtitle. */
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
.pos-hero > * { position: relative; z-index: 1; }
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

/* Floating labels on the sign-in page (decorative; hidden on small screens). */
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
}
.pos-float.coral { background: #ffd9cc; border-color: #ffc3b0; }
.pos-float.blue { background: var(--bubble); border-color: #c9d7ff; }
@media (max-width: 900px) { .pos-floats { display: none; } }

/* Cards: st.container(key="card-…"). */
[class*="st-key-card"] {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 1.1rem 1.2rem;
  box-shadow: var(--shadow);
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

/* Goal meter: a slim same-hue track with the done share (matches the dashboard). */
.pos-meter { display: flex; align-items: center; gap: 0.75rem; margin: 0.35rem 0 0.2rem; }
.pos-meter-track {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: #dbe7fb;
  overflow: hidden;
}
.pos-meter-fill { height: 100%; border-radius: 999px; background: #2a78d6; }
.pos-meter-label { font-size: 0.82rem; color: var(--ink-2); min-width: 5.5rem; text-align: right; }

/* Metrics as tiles with a small uppercase label. */
[data-testid="stMetric"] {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 1rem 1.15rem 0.9rem;
  box-shadow: var(--shadow);
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
}
[data-testid="stChatInputSubmitButton"] {
  background: var(--accent);
  border-radius: 999px;
  color: #fff;
}
[data-testid="stChatInputSubmitButton"]:disabled { background: #f3c4b6; }

/* Tabs: underline in ink, quieter labels. */
[data-testid="stTab"] p { font-size: 0.92rem; }
[data-testid="stTabs"] [aria-selected="true"] p { color: var(--ink); font-weight: 600; }

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
