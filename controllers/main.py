# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pprint

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils


_logger = logging.getLogger(__name__)


class MoamalatController(http.Controller):
    _return_url = '/payment/moamalat/return'
    _webhook_url = '/payment/moamalat/webhook'
    _secure_hash_url = '/payment/moamalat/secure_hash'

    @http.route(_secure_hash_url, type='json', auth='public', methods=['POST'])
    def moamalat_get_secure_hash(self, provider_id, amount, reference, **kwargs):
        """Generate secure hash for payment request.

        This endpoint is called by the frontend JavaScript before initiating
        the Lightbox payment to get a fresh secure hash.

        :param int provider_id: The payment provider ID
        :param float amount: The payment amount
        :param str reference: The merchant reference (transaction reference)
        :return: Dict with secure_hash and datetime_local
        :rtype: dict
        """
        provider_sudo = request.env['payment.provider'].sudo().browse(provider_id)
        if not provider_sudo.exists() or provider_sudo.code != 'moamalat':
            raise ValidationError(_("Invalid payment provider."))

        # Get the transaction to verify the amount
        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('reference', '=', reference),
            ('provider_id', '=', provider_id),
        ], limit=1)

        if not tx_sudo:
            raise ValidationError(_("Transaction not found."))

        # Convert amount to minor units
        amount_minor = payment_utils.to_minor_currency_units(
            tx_sudo.amount, tx_sudo.currency_id
        )

        # Generate secure hash
        hash_data = provider_sudo._moamalat_generate_secure_hash(
            amount=amount_minor,
            merchant_reference=reference,
        )

        return {
            'secure_hash': hash_data['secure_hash'],
            'datetime_local': hash_data['datetime_local'],
            'amount': amount_minor,
            'merchant_id': provider_sudo.moamalat_merchant_id,
            'terminal_id': provider_sudo.moamalat_terminal_id,
        }

    @http.route(_return_url, type='http', methods=['GET', 'POST'], auth='public', csrf=False)
    def moamalat_return(self, **data):
        """Process the return from Moamalat Lightbox.

        This is called when the customer completes the payment in the Lightbox
        and is redirected back or when the Lightbox sends a callback.

        :param dict data: The callback data from Moamalat Lightbox
        """
        _logger.info("Moamalat return with data:\n%s", pprint.pformat(data))

        # Extract reference from callback data
        reference = data.get('MerchantReference') or data.get('reference')
        if not reference:
            _logger.warning("Moamalat return without reference")
            return request.redirect('/payment/status')

        # Find the transaction
        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('reference', '=', reference),
            ('provider_code', '=', 'moamalat'),
        ], limit=1)

        if tx_sudo:
            # Process the direct payment callback
            tx_sudo._moamalat_process_direct_payment(data)

        return request.redirect('/payment/status')

    @http.route(_webhook_url, type='http', methods=['POST'], auth='public', csrf=False)
    def moamalat_webhook(self):
        """Process webhook notifications from Moamalat.

        Moamalat sends transaction status updates to this endpoint.
        The notification includes transaction details and a SecureHash for verification.

        :return: JSON response acknowledging the notification
        :rtype: Response
        """
        # Get JSON data from request
        try:
            data = request.get_json_data()
        except Exception:
            # Try form data if JSON parsing fails
            data = dict(request.httprequest.form)

        _logger.info("Moamalat webhook received:\n%s", pprint.pformat(data))

        try:
            # Extract key fields
            merchant_id = data.get('MerchantId')
            terminal_id = data.get('TerminalId')
            merchant_reference = data.get('MerchantReference')
            secure_hash = data.get('SecureHash')
            amount = data.get('Amount')
            currency = data.get('Currency')
            datetime_local = data.get('DateTimeLocalTrxn')

            if not merchant_reference:
                _logger.warning("Moamalat webhook without MerchantReference")
                return request.make_json_response({
                    'Message': 'Missing MerchantReference',
                    'Success': False,
                })

            # Find matching provider
            provider_sudo = request.env['payment.provider'].sudo().search([
                ('code', '=', 'moamalat'),
                ('moamalat_merchant_id', '=', merchant_id),
                ('moamalat_terminal_id', '=', terminal_id),
            ], limit=1)

            if not provider_sudo:
                _logger.warning(
                    "Moamalat webhook: No provider found for MID=%s, TID=%s",
                    merchant_id, terminal_id
                )
                return request.make_json_response({
                    'Message': 'Provider not found',
                    'Success': False,
                })

            # Verify the secure hash
            verified = provider_sudo._moamalat_verify_notification_hash(
                secure_hash=secure_hash,
                amount=amount,
                currency=currency,
                datetime_local=datetime_local,
                merchant_id=merchant_id,
                terminal_id=terminal_id,
            )

            # Add verification status to notification data
            data['verified'] = verified

            # Find and process the transaction
            tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
                'moamalat', data
            )
            tx_sudo._handle_notification_data('moamalat', data)

            return request.make_json_response({
                'Message': 'Success',
                'Success': True,
            })

        except ValidationError as e:
            _logger.exception("Moamalat webhook validation error: %s", e)
            return request.make_json_response({
                'Message': str(e),
                'Success': False,
            })
        except Exception as e:
            _logger.exception("Moamalat webhook error: %s", e)
            return request.make_json_response({
                'Message': 'Internal error',
                'Success': False,
            })

    @http.route('/payment/moamalat/callback', type='json', auth='public', methods=['POST'])
    def moamalat_callback(self, reference, **data):
        """Process callback from Lightbox JavaScript.

        This is called by the frontend when the Lightbox completes.
        The completeCallback, errorCallback, or cancelCallback from
        the Lightbox triggers this endpoint.

        :param str reference: The transaction reference
        :param dict data: The callback data from Lightbox
        :return: Dict with status and redirect URL
        :rtype: dict
        """
        _logger.info(
            "Moamalat callback for reference %s:\n%s",
            reference, pprint.pformat(data)
        )

        # Find the transaction
        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('reference', '=', reference),
            ('provider_code', '=', 'moamalat'),
        ], limit=1)

        if not tx_sudo:
            raise ValidationError(_("Transaction not found."))

        # Process the callback
        tx_sudo._moamalat_process_direct_payment(data)

        return {
            'status': 'success' if tx_sudo.state == 'done' else tx_sudo.state,
            'redirect_url': '/payment/status',
        }
