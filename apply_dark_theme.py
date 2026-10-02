"""Run from the repo root:  python apply_dark_theme.py
Replaces the dark-mode colours in static/css/theme.css and points the dark
background at static/images/paradise.jpg (put paradise.jpg there first)."""
import os, re, sys

CSS = os.path.join('static', 'css', 'theme.css')
IMG = os.path.join('static', 'images', 'paradise.jpg')

if not os.path.exists(CSS):
    sys.exit('Run this from the repo root (static/css/theme.css not found).')
if not os.path.exists(IMG):
    print('WARNING: static/images/paradise.jpg is missing - copy it there too.')

NEW = '''[data-theme="dark"] {
  /* Dark palette sampled from the bird-of-paradise photo (static/images/paradise.jpg):
     black ground, flame orange and yellow petals, rust-red sepals, deep blue
     bracts and the green leaf. */
  --color-primary: #F28A1E;      /* flame orange petals */
  --color-primary-soft: #FFC266;
  --color-secondary: #F5C400;    /* yellow centre petal */
  --color-secondary-soft: #FFE27A;
  --color-accent: #4DA6D6;       /* blue bracts, lifted for contrast on black */
  --color-bg: #05070A;           /* the photo's black */
  --color-surface: #140D09;      /* burnt-brown black */
  --color-surface-alt: #22140D;
  --color-text: #FFF1E2;
  --color-text-muted: #C2A48E;
  --color-border: #4A2411;       /* rust sepal */

  --status-success: #7DC243;     /* leaf green */
  --status-warning: #F5C400;
  --status-danger: #EB4B2D;      /* red-orange */
  --status-info: #4DA6D6;

  --color-accent-alt-1: #1B0F0A;
  --color-accent-alt-2: #4A2411;

  --bg-image: url("/static/images/paradise.jpg");
}'''

raw = open(CSS, 'rb').read().decode('utf-8')
crlf = '\r\n' in raw
text = raw.replace('\r\n', '\n')
new_text, n = re.subn(r'\[data-theme="dark"\] \{.*?\n\}', lambda m: NEW, text, count=1, flags=re.S)
if n != 1:
    sys.exit('Could not find the [data-theme="dark"] block.')
if crlf:
    new_text = new_text.replace('\n', '\r\n')
open(CSS, 'wb').write(new_text.encode('utf-8'))
print('Done: dark theme updated in', CSS)