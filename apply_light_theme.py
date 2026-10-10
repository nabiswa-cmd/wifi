"""Run from the repo root:  python apply_light_theme.py
Light mode -> blue palette from the blue-waves picture. Put 5883.jpg in
static/images/ first. Safe to run twice."""
import os, re, sys

CSS = os.path.join('static', 'css', 'theme.css')
BASE = os.path.join('templates', 'core', 'admin_base.html')
IMG = os.path.join('static', 'images', '5883.jpg')
if not os.path.exists(CSS):
    sys.exit('Run this from the repo root (static/css/theme.css not found).')
if not os.path.exists(IMG):
    print('WARNING: static/images/5883.jpg is missing - copy it there too.')

LIGHT = ''':root,
[data-theme="light"] {
  /* Light palette sampled from the blue-waves picture (static/images/5883.jpg):
     pale sky-blue ground, white glow, mid blue ribbons, deep navy edges. */
  --color-primary: #1B6FB5;      /* ribbon blue */
  --color-primary-soft: #A9D4EC;
  --color-secondary: #0E4C8A;    /* deep navy fold */
  --color-secondary-soft: #CFE8F4;
  --color-accent: #4AA3D6;       /* light ribbon blue */
  --color-on-primary: #FFFFFF;   /* text on primary-coloured buttons / active chips */
  --color-bg: #EEF6FB;
  --color-surface: #FFFFFF;
  --color-surface-alt: #E4F1F9;
  --color-text: #0F2A44;
  --color-text-muted: #53708A;
  --color-border: #CFE3F0;

  --status-success: #1F9E6B;
  --status-warning: #D9A400;
  --status-danger: #D64545;
  --status-info: #3A9AD4;

  --bg-image: url("/static/images/5883.jpg");
}'''

def read(p):
    return open(p, 'rb').read().decode('utf-8')

def write(p, text):
    open(p, 'wb').write(text.encode('utf-8'))

def patch_css(path):
    t = read(path)
    m = re.search(r':root,\r?\n\[data-theme="light"\] \{.*?\r?\n\}', t, flags=re.S)
    if not m:
        print('  !! light block not found in', path)
        return
    eol = '\r\n' if '\r\n' in m.group(0) else '\n'
    t = t[:m.start()] + LIGHT.replace('\n', eol) + t[m.end():]
    d0 = t.index('[data-theme="dark"]')
    d1 = t.index('}', d0)
    if '--color-on-primary' not in t[d0:d1]:
        t = re.sub(r'(  --color-accent: #4DA6D6;)', '  --color-on-primary: #1c2410;' + eol + r'\1', t, count=1)
    t = t.replace('background: var(--color-primary); color: #1c2410;', 'background: var(--color-primary); color: var(--color-on-primary);')
    write(path, t)
    print('  updated', path)

patch_css(CSS)
# The collected copy that WhiteNoise may serve (committed in this repo)
COLLECTED = os.path.join('staticfiles', 'css', 'theme.css')
if os.path.exists(COLLECTED):
    patch_css(COLLECTED)

b = read(BASE).replace('color: #1c2410;', 'color: var(--color-on-primary);')
write(BASE, b)
print('  updated', BASE)

# Cache-bust so browsers stop using the old theme.css
ROOT_TPL = os.path.join('templates', 'base.html')
if os.path.exists(ROOT_TPL):
    h = read(ROOT_TPL)
    h2 = re.sub(r"(css/theme\.css' %\}\?v=)\d+", lambda m: m.group(1) + '6', h)
    if h2 != h:
        write(ROOT_TPL, h2)
        print('  bumped theme.css cache version in', ROOT_TPL)

print('Done: light theme updated. Now commit, push and redeploy.')