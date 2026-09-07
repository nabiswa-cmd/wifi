"""
Daraja (M-Pesa) client. This is the ONLY place in the codebase that talks
to Safaricom's API — billing/views.py just calls stk_push() and reacts to
success/failure, it never builds a Daraja payload itself.
"""
import base64
import datetime
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URLS = {
    'sandbox': 'https://sandbox.safaricom.co.ke',
    'production': 'https://api.safaricom.co.ke',
}


class MpesaError(Exception):
    """Raised whenever Daraja rejects the auth request or the STK push
    itself. Never raised for 'customer cancelled/entered wrong PIN' —
    that's a normal callback outcome, handled in views.mpesa_callback."""


def _base_url() -> str:
    return BASE_URLS.get(settings.MPESA_ENV, BASE_URLS['sandbox'])


def get_access_token() -> str:
    url = f'{_base_url()}/oauth/v1/generate?grant_type=client_credentials'
    resp = requests.get(
        url,
        auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
        timeout=10,
    )
    if resp.status_code != 200:
        raise MpesaError(f'Could not authenticate with Daraja ({resp.status_code}): {resp.text}')
    return resp.json()['access_token']


def _password_and_timestamp():
    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    raw = f'{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}'
    password = base64.b64encode(raw.encode()).decode()
    return password, timestamp


def stk_push(*, phone_number: str, amount, account_reference: str, transaction_desc: str) -> dict:
    """
    Sends the actual STK push to the customer's phone. Returns Daraja's
    JSON response (contains CheckoutRequestID/MerchantRequestID). This
    response only confirms the *prompt was sent* — never treat it as proof
    of payment. Only apps.billing.views.mpesa_callback, driven by
    Safaricom's callback, may call Payment.mark_success().
    """
    token = get_access_token()
    password, timestamp = _password_and_timestamp()

    payload = {
        'BusinessShortCode': settings.MPESA_SHORTCODE,   # HO/store number (auth)
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': settings.MPESA_TRANSACTION_TYPE,  # CustomerBuyGoodsOnline
        'Amount': int(amount),
        'PartyA': phone_number,                          # customer, 2547XXXXXXXX
        'PartyB': settings.MPESA_PARTY_B,                 # actual till number
        'PhoneNumber': phone_number,
        'CallBackURL': settings.MPESA_CALLBACK_URL,
        'AccountReference': account_reference[:12],       # Daraja caps this field
        'TransactionDesc': transaction_desc[:20],
    }

    resp = requests.post(
        f'{_base_url()}/mpesa/stkpush/v1/processrequest',
        json=payload,
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    try:
        data = resp.json()
    except ValueError:
        raise MpesaError(f'Non-JSON response from Daraja: {resp.status_code} {resp.text}')

    if resp.status_code != 200 or str(data.get('ResponseCode')) != '0':
        logger.error('STK push rejected by Daraja: %s', data)
        raise MpesaError(data.get('errorMessage') or data.get('ResponseDescription') or 'STK push failed')

    return data
