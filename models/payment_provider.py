# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import json
import logging
import time

import requests
from werkzeug.urls import url_join

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_moamalat import const


_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('moamalat', "Moamalat")],
        ondelete={'moamalat': 'set default'},
    )
    moamalat_merchant_id = fields.Char(
        string="Merchant ID (MID)",
        help="The Merchant ID provided by Moamalat Bank",
        required_if_provider='moamalat',
    )
    moamalat_terminal_id = fields.Char(
        string="Terminal ID (TID)",
        help="The Terminal ID provided by Moamalat Bank",
        required_if_provider='moamalat',
    )
    moamalat_secure_key = fields.Char(
        string="Secure Key",
        help="The secure key for generating payment hashes",
        required_if_provider='moamalat',
        groups='base.group_system',
    )
    moamalat_notification_key = fields.Char(
        string="Notification Key",
        help="The key for verifying webhook notifications from Moamalat",
        groups='base.group_system',
    )

    # === COMPUTE METHODS === #

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'moamalat').update({
            'support_express_checkout': False,
            'support_manual_capture': False,
            'support_refund': 'partial',
            'support_tokenization': False,
        })

    # === BUSINESS METHODS === #

    def _moamalat_get_api_url(self):
        """Return the API URL based on the provider state.

        :return: The API URL (production or test)
        :rtype: str
        """
        self.ensure_one()
        if self.state == 'enabled':
            return 'https://npg.moamalat.net'
        return 'https://tnpg.moamalat.net'

    def _moamalat_get_lightbox_url(self):
        """Return the Lightbox script URL based on the provider state.

        :return: The Lightbox script URL
        :rtype: str
        """
        self.ensure_one()
        base_url = self._moamalat_get_api_url()
        return f'{base_url}:6006/js/lightbox.js'

    def _moamalat_generate_secure_hash(self, amount, merchant_reference, datetime_local=None):
        """Generate the secure hash for a payment request.

        :param int amount: The payment amount in minor units
        :param str merchant_reference: The merchant reference for the transaction
        :param int datetime_local: The local transaction datetime (timestamp), optional
        :return: A dict containing the secure hash and datetime
        :rtype: dict
        """
        self.ensure_one()

        if datetime_local is None:
            datetime_local = int(time.time())

        # Build the data string for hashing
        encode_data = (
            f"Amount={amount}"
            f"&DateTimeLocalTrxn={datetime_local}"
            f"&MerchantId={self.moamalat_merchant_id}"
            f"&MerchantReference={merchant_reference}"
            f"&TerminalId={self.moamalat_terminal_id}"
        )

        # Convert hex key to binary
        key = bytes.fromhex(self.moamalat_secure_key)

        # Generate HMAC-SHA256 hash
        secure_hash = hmac.new(key, encode_data.encode(), hashlib.sha256).hexdigest()

        return {
            'secure_hash': secure_hash,
            'datetime_local': datetime_local,
        }

    def _moamalat_verify_notification_hash(self, secure_hash, amount, currency, datetime_local,
                                            merchant_id, terminal_id):
        """Verify the secure hash from a webhook notification.

        :param str secure_hash: The hash received in the notification
        :param str amount: The transaction amount
        :param str currency: The currency code
        :param str datetime_local: The transaction datetime
        :param str merchant_id: The merchant ID
        :param str terminal_id: The terminal ID
        :return: Whether the hash is valid
        :rtype: bool
        """
        self.ensure_one()

        if not self.moamalat_notification_key:
            _logger.warning("Moamalat notification key not configured")
            return False

        try:
            encode_data = (
                f"Amount={amount}"
                f"&Currency={currency}"
                f"&DateTimeLocalTrxn={datetime_local}"
                f"&MerchantId={merchant_id}"
                f"&TerminalId={terminal_id}"
            )

            key = bytes.fromhex(self.moamalat_notification_key)
            expected_hash = hmac.new(key, encode_data.encode(), hashlib.sha256).hexdigest()

            return expected_hash.upper() == secure_hash.upper()
        except Exception as e:
            _logger.exception("Error verifying Moamalat notification hash: %s", e)
            return False

    def _moamalat_get_inline_form_values(self, amount, currency, partner_id, **kwargs):
        """Return a serialized JSON of the required values to render the inline form.

        :param float amount: The amount in major units
        :param res.currency currency: The currency of the transaction
        :param int partner_id: The partner of the transaction
        :return: The JSON serial of the required values to render the inline form
        :rtype: str
        """
        self.ensure_one()

        # Get currency code for Moamalat
        currency_code = const.CURRENCY_MAPPING.get(currency.name, '434')  # Default to LYD

        inline_form_values = {
            'merchant_id': self.moamalat_merchant_id,
            'terminal_id': self.moamalat_terminal_id,
            'lightbox_url': self._moamalat_get_lightbox_url(),
            'currency_code': currency_code,
            'is_production': self.state == 'enabled',
        }
        return json.dumps(inline_form_values)

    def _moamalat_make_request(self, endpoint, payload=None):
        """Make a request to Moamalat API.

        :param str endpoint: The API endpoint
        :param dict payload: The request payload
        :return: The JSON response
        :rtype: dict
        """
        self.ensure_one()

        url = url_join(self._moamalat_get_api_url(), f'/cube/paylink.svc/api/{endpoint}')

        try:
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError:
            _logger.exception("Unable to reach Moamalat API at %s", url)
            raise ValidationError(_("Could not establish connection to Moamalat API."))
        except requests.exceptions.HTTPError:
            _logger.exception("Invalid API request at %s with data %s", url, payload)
            raise ValidationError(_("An error occurred when communicating with Moamalat API."))

    def _moamalat_refund_transaction(self, system_reference=None, network_reference=None,
                                      amount=None):
        """Refund a transaction via Moamalat API.

        :param str system_reference: The system reference of the original transaction
        :param str network_reference: The network reference of the original transaction
        :param int amount: The refund amount
        :return: The refund response
        :rtype: dict
        """
        self.ensure_one()

        datetime_local = int(time.time())

        # Generate secure hash for refund
        encode_data = (
            f"DateTimeLocalTrxn={datetime_local}"
            f"&MerchantId={self.moamalat_merchant_id}"
            f"&TerminalId={self.moamalat_terminal_id}"
        )
        key = bytes.fromhex(self.moamalat_secure_key)
        secure_hash = hmac.new(key, encode_data.encode(), hashlib.sha256).hexdigest()

        payload = {
            'TerminalId': self.moamalat_terminal_id,
            'MerchantId': self.moamalat_merchant_id,
            'DateTimeLocalTrxn': datetime_local,
            'SecureHash': secure_hash,
            'AmountTrxn': amount,
        }

        if system_reference:
            payload['SystemReference'] = system_reference
        if network_reference:
            payload['NetworkReference'] = network_reference

        return self._moamalat_make_request('RefundTransaction', payload)

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'moamalat':
            return default_codes
        return ['card']
