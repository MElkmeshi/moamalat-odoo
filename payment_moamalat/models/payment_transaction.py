# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pprint

from odoo import _, models
from odoo.exceptions import ValidationError

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_moamalat import const


_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_processing_values(self, processing_values):
        """Override of payment to return Moamalat-specific processing values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic processing values of the transaction
        :return: The dict of provider-specific processing values
        :rtype: dict
        """
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != 'moamalat':
            return res

        # Convert amount to minor units (no decimals for Moamalat)
        amount = payment_utils.to_minor_currency_units(self.amount, self.currency_id)

        # Generate secure hash
        hash_data = self.provider_id._moamalat_generate_secure_hash(
            amount=amount,
            merchant_reference=self.reference,
        )

        return {
            'amount': amount,
            'merchant_reference': self.reference,
            'secure_hash': hash_data['secure_hash'],
            'datetime_local': hash_data['datetime_local'],
            'merchant_id': self.provider_id.moamalat_merchant_id,
            'terminal_id': self.provider_id.moamalat_terminal_id,
        }

    def _send_refund_request(self, amount_to_refund=None):
        """Override of payment to send a refund request to Moamalat.

        Note: self.ensure_one()

        :param float amount_to_refund: The amount to refund.
        :return: The refund transaction created to process the refund request.
        :rtype: recordset of `payment.transaction`
        """
        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)
        if self.provider_code != 'moamalat':
            return refund_tx

        # Convert amount to minor units
        amount = payment_utils.to_minor_currency_units(
            -refund_tx.amount,  # Refund transactions' amount is negative
            refund_tx.currency_id,
        )

        # Make the refund request
        try:
            response = self.provider_id._moamalat_refund_transaction(
                system_reference=self.provider_reference,
                amount=amount,
            )
            _logger.info(
                "Refund request response for transaction with reference %s:\n%s",
                self.reference, pprint.pformat(response)
            )

            # Handle the refund response
            if response.get('Success'):
                refund_tx._set_done()
                refund_tx.provider_reference = response.get('RefNumber')
            else:
                refund_tx._set_error(response.get('Message', 'Refund failed'))
        except Exception as e:
            _logger.exception("Refund request failed: %s", e)
            refund_tx._set_error(str(e))

        return refund_tx

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """Override of payment to find the transaction based on Moamalat data.

        :param str provider_code: The code of the provider that handled the transaction
        :param dict notification_data: The notification data sent by the provider
        :return: The transaction if found
        :rtype: recordset of `payment.transaction`
        :raise: ValidationError if the data match no transaction
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'moamalat' or len(tx) == 1:
            return tx

        reference = notification_data.get('MerchantReference')
        if not reference:
            raise ValidationError(
                "Moamalat: " + _("Received data with missing merchant reference.")
            )

        tx = self.search([
            ('reference', '=', reference),
            ('provider_code', '=', 'moamalat'),
        ])
        if not tx:
            raise ValidationError(
                "Moamalat: " + _("No transaction found matching reference %s.", reference)
            )
        return tx

    def _process_notification_data(self, notification_data):
        """Override of `payment` to process the transaction based on Moamalat data.

        Note: self.ensure_one()

        :param dict notification_data: The notification data from Moamalat webhook
        :return: None
        :raise: ValidationError if inconsistent data were received
        """
        super()._process_notification_data(notification_data)
        if self.provider_code != 'moamalat':
            return

        # Extract key data from notification
        action_code = notification_data.get('ActionCode')
        system_reference = notification_data.get('SystemReference')
        network_reference = notification_data.get('NetworkReference')
        txn_type = notification_data.get('TxnType')
        verified = notification_data.get('verified', False)

        # Store provider reference
        self.provider_reference = system_reference or network_reference

        _logger.info(
            "Processing Moamalat notification for transaction %s: ActionCode=%s, TxnType=%s, Verified=%s",
            self.reference, action_code, txn_type, verified
        )

        # Check if transaction is verified
        if not verified:
            self._set_error(_("Transaction verification failed. The payment could not be verified."))
            return

        # Process based on action code
        if action_code == const.ACTION_CODE_APPROVED:
            # Transaction approved
            if txn_type == const.TXN_TYPE_SALE:
                self._set_done()
            elif txn_type == const.TXN_TYPE_REFUND:
                self._set_done()
            elif txn_type in (const.TXN_TYPE_VOID_SALE, const.TXN_TYPE_VOID_REFUND):
                self._set_canceled()
            else:
                self._set_done()
        else:
            # Transaction failed
            message = notification_data.get('Message', _("Payment was declined."))
            self._set_error(message)

    def _moamalat_process_direct_payment(self, notification_data):
        """Process a direct payment callback from Lightbox.

        This is called when the payment is completed through the Lightbox UI
        and the frontend sends the result to our callback endpoint.

        :param dict notification_data: The payment result from Lightbox
        """
        self.ensure_one()

        _logger.info(
            "Processing Moamalat direct payment for transaction %s:\n%s",
            self.reference, pprint.pformat(notification_data)
        )

        # Extract data from Lightbox callback
        system_reference = notification_data.get('SystemReference')
        network_reference = notification_data.get('NetworkReference')

        # Store provider reference
        if system_reference:
            self.provider_reference = system_reference
        elif network_reference:
            self.provider_reference = network_reference

        # Check if payment was successful
        # Lightbox typically returns status in different formats
        status = notification_data.get('status') or notification_data.get('Status')
        message = notification_data.get('message') or notification_data.get('Message')

        if status == 'success' or message == 'Approved':
            self._set_done()
        elif status == 'cancelled' or status == 'cancel':
            self._set_canceled()
        else:
            error_message = message or _("Payment was not completed.")
            self._set_error(error_message)
