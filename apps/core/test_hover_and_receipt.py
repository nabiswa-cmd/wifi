"""
Guards for three look-and-feel rules that are easy to break by accident:

  1. Admin dashboard tiles fill with their own colour on hover (no rising underline),
     and the text on that fill stays readable in BOTH themes. The contrast check reads
     the real colours from static/css/theme.css, so if the palette is changed later and
     a tile would become hard to read, this fails.
  2. Checkout "Pay with" buttons fill on hover with dark text (no rising underline).
  3. The waiting screen shows the M-Pesa receipt with a Copy button once payment succeeds.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role, StaffProfile, User
from apps.core.models import SystemSettings
from apps.packages.models import InternetPackage

TONE_VARS = {  # tile tone -> the theme variable it uses (see .tone-* in admin_base.html)
    'blue': '--status-info', 'green': '--status-success', 'amber': '--status-warning',
    'red': '--status-danger', 'teal': '--color-primary',
}
PURPLE = '#8b5cf6'
TINT = 0.28  # must match the percentage in admin_base.html


def _rgb(hex_):
    hex_ = hex_.lstrip('#')
    return tuple(int(hex_[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a, b):
    hi, lo = sorted((_lum(_rgb(a) if isinstance(a, str) else a), _lum(_rgb(b) if isinstance(b, str) else b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(fg, bg, pct):
    f, b = _rgb(fg), _rgb(bg)
    return tuple(round(f[i] * pct + b[i] * (1 - pct)) for i in range(3))


def _theme_vars():
    css = Path(settings.BASE_DIR, 'static/css/theme.css').read_text(encoding='utf-8')
    blocks = {}
    for name, selector in (('light', '[data-theme="light"] {'), ('dark', '[data-theme="dark"] {')):
        start = css.index(selector)
        body = css[start:css.index('}', start)]
        blocks[name] = dict(re.findall(r'(--[\w-]+):\s*(#[0-9A-Fa-f]{6})', body))
    return blocks


class AdminTileHoverTests(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        user = User.objects.create_user(username='boss', password='pw12345!')
        role, _ = Role.objects.get_or_create(name='SUPER_ADMIN')
        StaffProfile.objects.update_or_create(user=user, defaults={'role': role})
        self.client.force_login(user)

    def test_whole_tile_changes_colour_and_the_underline_is_gone(self):
        html = self.client.get(reverse('core:fup_monitor')).content.decode()
        self.assertIn('a.stat-tile:hover', html)
        self.assertRegex(html, r'color-mix\(in srgb, var\(--tone-c\) 28%, var\(--color-surface\)\)')
        self.assertNotIn('a.stat-tile::after', html)

    def test_text_on_the_hovered_tile_is_readable_in_both_themes(self):
        themes = _theme_vars()
        for theme, v in themes.items():
            tones = {n: v[var] for n, var in TONE_VARS.items()}
            tones['purple'] = PURPLE
            for name, tone in tones.items():
                bg = _mix(tone, v['--color-surface'], TINT)
                # Figures and (on hover) labels both use the main text colour: aim for AAA body text.
                self.assertGreaterEqual(_contrast(v['--color-text'], bg), 7.0, f'{theme}/{name} text on hovered tile')


class CheckoutButtonHoverTests(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        InternetPackage.objects.create(name='Day', price=100, duration=1, duration_unit='DAYS')
        self.html = self.client.get('/').content.decode()

    def test_pay_with_buttons_fill_on_hover_instead_of_underlining(self):
        self.assertNotIn('.bm-method::after', self.html)
        self.assertRegex(self.html, r'\.bm-method:hover, \.bm-method:focus-visible \{ background: var\(--bm-hover\)')

    def test_text_on_the_filled_button_is_dark_enough(self):
        fill = re.search(r'--bm-hover:\s*(#[0-9a-fA-F]{6})', self.html).group(1)
        text = re.search(r'\.bm-method:hover \.bm-method-text[^{]*\{ color: (#[0-9a-fA-F]{6})', self.html).group(1)
        self.assertGreaterEqual(_contrast(text, fill), 7.0)
        self.assertLess(_contrast('#ffffff', fill), 3.0)  # i.e. leaving the text white WOULD have been unreadable

    def test_secondary_buttons_in_the_modal_are_readable(self):
        # They used to be white text on a pale background in light mode.
        self.assertRegex(self.html, r'#buy-modal-overlay \.modal-card \.btn-secondary \{\s*background: rgba\(255, 255, 255, \.92\); color: #12233d')
        self.assertGreaterEqual(_contrast('#12233d', '#f5f8fb'), 7.0)


class WaitingScreenReceiptTests(TestCase):
    def setUp(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={})
        InternetPackage.objects.create(name='Day', price=100, duration=1, duration_unit='DAYS')
        self.html = self.client.get('/').content.decode()

    def test_receipt_block_with_copy_button_and_hint_is_in_the_loader(self):
        for marker in ('ll-receipt-code', 'll-copy', 'Your M-Pesa code', 'Taking too long?', 'Already paid via M-Pesa?'):
            self.assertIn(marker, self.html)

    def test_poller_hands_the_receipt_to_the_loader_when_payment_succeeds(self):
        self.assertIn('loader.showReceipt(data.receipt)', self.html)

    def test_page_offers_the_take_me_there_shortcut(self):
        self.assertIn('onGoReconnect', self.html)
        self.assertIn('reconnect-code-input', self.html)
