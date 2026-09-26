"""
Every outgoing email the shareholder/revenue portal sends, in one place.

Deliberately plain, best-effort sends: every function here wraps its
send_mail call in try/except and logs on failure rather than raising, so a
misconfigured or briefly-down mail server never turns an approval, a
withdrawal request, or an account update into a 500  the underlying action
(money marked paid, voucher generated, shares increased, etc.) has already
happened and must not be rolled back just because the notification about it
failed to send.

Branding (business name, support contact) is always read fresh from
SystemSettings rather than hard-coded, same rule as everywhere else in this
app (see core.models.SystemSettings docstring)  so these emails say
whatever the Company has set as the business name (e.g. "Logic Company")
without needing a code change if that ever changes.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _business_name() -> str:
    from apps.core.models import SystemSettings
    return SystemSettings.load().business_name or 'the Company'


def _currency() -> str:
    from apps.core.models import SystemSettings
    return SystemSettings.load().currency or ''


def _send(subject, message, recipient_list, fail_context='', heading=None):
    """
    Shared send wrapper. recipient_list entries that are blank are dropped
    first so a shareholder with no email on file never raises inside
    send_mail; if nothing is left to send to, this is a silent no-op.

    Sends BOTH a plain-text part (the `message` string, unchanged  used
    by clients/spam filters that prefer plain text) and an HTML part
    rendered from templates/emails/base_email.html, the ARTSASA-style
    card layout recolored to the light-mode brand palette. `message` is
    split on blank lines into paragraphs for the HTML body, so no
    existing send_* function has to change to get an HTML email.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from apps.core.models import SystemSettings

    recipients = [r for r in recipient_list if r]
    if not recipients:
        return

    business = _business_name()
    location = SystemSettings.load().location
    paragraphs = [p.strip() for p in message.strip().split('\n\n') if p.strip()]

    try:
        html_body = render_to_string('emails/base_email.html', {
            'subject': subject,
            'business_name': business,
            'tagline': 'INTERNET & WIFI SERVICES',
            'location': location,
            'heading': heading or subject,
            'paragraphs': paragraphs,
            'highlight': None,
        })
        email = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
        )
        email.attach_alternative(html_body, 'text/html')
        email.send(fail_silently=False)
    except Exception:
        logger.exception('Failed to send email (%s) to %s', fail_context, recipients)


def send_welcome_email(user, shareholder=None):
    """
    Sent whenever a shareholder sets or changes the email on their account
    (see core.views.my_account)  the first time this fires for someone is,
    in effect, their welcome-aboard email; if they change their email again
    later they simply get it again at the new address, which is fine.
    """
    business = _business_name()
    name = (shareholder.full_name if shareholder else '') or user.get_full_name() or user.username
    subject = f'Welcome to {business}'
    message = (
        f'Hi {name},\n\n'
        f'Your email has been confirmed on your {business} shareholder account. '
        f'You are officially part of {business}.\n\n'
        'From your shareholder portal you can track company revenue, your earnings, '
        'request withdrawals, and request a share increase at any time.\n\n'
        f'Welcome aboard,\n{business}'
    )
    _send(subject, message, [user.email], fail_context='welcome email')


def send_withdrawal_approved_email(withdrawal):
    """
    Sent to the shareholder once the Company marks their withdrawal
    request PAID (see billing.models.WithdrawalRequest.approve_and_pay).
    """
    business = _business_name()
    currency = _currency()
    shareholder = withdrawal.shareholder
    user = shareholder.user
    subject = f'Your withdrawal request has been approved  {business}'
    message = (
        f'Hi {shareholder.full_name or user.get_username()},\n\n'
        f'Your withdrawal request for {currency} {withdrawal.amount} has been approved and the '
        f'payment has been sent to {withdrawal.payment_phone}'
        f'{" (" + withdrawal.payment_account_name + ")" if withdrawal.payment_account_name else ""}.\n\n'
        f'Period: {withdrawal.period_start:%B %Y}\n\n'
        f'Thank you,\n{business}'
    )
    _send(subject, message, [user.email], fail_context='withdrawal approved email')


def send_withdrawal_rejected_email(withdrawal):
    """Sent to the shareholder if the Company rejects their withdrawal request."""
    business = _business_name()
    currency = _currency()
    shareholder = withdrawal.shareholder
    user = shareholder.user
    subject = f'Your withdrawal request was not approved  {business}'
    message = (
        f'Hi {shareholder.full_name or user.get_username()},\n\n'
        f'Your withdrawal request for {currency} {withdrawal.amount} was not approved.\n'
        + (f'Reason: {withdrawal.rejection_reason}\n' if withdrawal.rejection_reason else '')
        + f'\nYou can submit a new request from your shareholder portal.\n\n{business}'
    )
    _send(subject, message, [user.email], fail_context='withdrawal rejected email')


def send_new_withdrawal_admin_notification(withdrawal):
    """
    Fired on every new withdrawal request (approved or not), straight to
    settings.ADMIN_NOTIFICATION_EMAIL, so the Company hears about a
    pending request even if he isn't watching the portal.
    """
    business = _business_name()
    currency = _currency()
    shareholder = withdrawal.shareholder
    subject = f'New pending withdrawal request  {business}'
    message = (
        f'{shareholder.full_name or shareholder.user.get_username()} has requested a withdrawal '
        f'of {currency} {withdrawal.amount} for {withdrawal.period_start:%B %Y}.\n\n'
        f'Send to: {withdrawal.payment_phone}'
        f'{" (" + withdrawal.payment_account_name + ")" if withdrawal.payment_account_name else ""}\n'
        + (f'Note: {withdrawal.note}\n' if withdrawal.note else '')
        + '\nApprove or reject it from Withdrawal Requests in the admin portal.'
    )
    _send(subject, message, [settings.ADMIN_NOTIFICATION_EMAIL], fail_context='admin withdrawal notification')


def send_voucher_batch_approved_email(batch):
    """Sent to whoever requested a voucher batch once the Company approves it."""
    business = _business_name()
    requester = batch.created_by
    if not requester or not requester.email:
        return
    subject = f'Your voucher request has been approved  {business}'
    message = (
        f'Hi {requester.get_full_name() or requester.get_username()},\n\n'
        f'Your request for {batch.quantity} x {batch.package.name} voucher(s) has been approved '
        f'and the codes have been generated.\n\n'
        f'Sign in to the shareholder portal to view/copy the code(s).\n\n{business}'
    )
    _send(subject, message, [requester.email], fail_context='voucher approved email')


def send_share_increase_approved_email(increase_request):
    """
    Sent to EVERY active shareholder (not just the one whose stake grew)
    once a share-increase request is approved, since it changes every
    shareholder's percentage (see billing.models.ShareIncreaseRequest.approve).
    """
    from apps.billing.models import Shareholder

    business = _business_name()
    currency = _currency()
    grown = increase_request.shareholder
    subject = f'Shareholding update  {business}'
    message = (
        f'This is to notify all shareholders that a share increase request from '
        f'{grown.full_name or grown.user.get_username()} has been approved: '
        f'+{increase_request.share_quantity} share(s) funded by a contribution of '
        f'{currency} {increase_request.contribution_amount}.\n\n'
        'Total capital invested and every shareholder\'s percentage have been updated '
        'accordingly. Sign in to the shareholder portal to see your current standing.\n\n'
        f'{business}'
    )
    recipients = [
        sh.user.email
        for sh in Shareholder.objects.filter(is_active=True).select_related('user')
    ]
    _send(subject, message, recipients, fail_context='share increase approved email')


def send_custom_email(subject, message, recipients, sender_name=''):
    """
    Backs the Main-Admin-only "Send Email" page (core.views.compose_email)
    used for ad-hoc announcements  meeting notices and the like  to any
    chosen set of shareholders.
    """
    business = _business_name()
    footer = f'\n\n {business}' + (f', from {sender_name}' if sender_name else '')
    _send(subject, message + footer, recipients, fail_context='custom email')
