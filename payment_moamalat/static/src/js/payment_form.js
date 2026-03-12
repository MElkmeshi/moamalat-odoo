/** @odoo-module */
/* global Lightbox */

import { _t } from '@web/core/l10n/translation';
import paymentForm from '@payment/js/payment_form';
import { rpc, RPCError } from '@web/core/network/rpc';


paymentForm.include({

    moamalatFormValues: undefined,
    moamalatLightboxLoaded: false,

    // #=== DOM MANIPULATION ===#

    /**
     * Prepare the inline form of Moamalat for direct payment.
     *
     * @override method from @payment/js/payment_form
     * @private
     * @param {number} providerId - The id of the selected payment option's provider.
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {string} flow - The online payment flow of the selected payment option.
     * @return {void}
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'moamalat') {
            this._super(...arguments);
            return;
        }

        // No inline form for tokens
        if (flow === 'token') {
            return;
        }

        // Overwrite the flow of the selected payment method
        this._setPaymentFlow('direct');

        // Extract and deserialize the inline form values
        const radio = document.querySelector('input[name="o_payment_radio"]:checked');
        const inlineForm = this._getInlineForm(radio);
        const moamalatContainer = inlineForm.querySelector('[name="o_moamalat_element_container"]');

        if (!moamalatContainer) {
            return;
        }

        this.moamalatFormValues = JSON.parse(
            moamalatContainer.dataset['moamalatInlineFormValues']
        );

        // Load the Lightbox script if not already loaded
        if (!this.moamalatLightboxLoaded) {
            await this._loadMoamalatLightbox(this.moamalatFormValues['lightbox_url']);
        }
    },

    /**
     * Load the Moamalat Lightbox script dynamically.
     *
     * @private
     * @param {string} lightboxUrl - The URL of the Lightbox script
     * @return {Promise}
     */
    _loadMoamalatLightbox(lightboxUrl) {
        return new Promise((resolve, reject) => {
            if (typeof Lightbox !== 'undefined') {
                this.moamalatLightboxLoaded = true;
                resolve();
                return;
            }

            const script = document.createElement('script');
            script.src = lightboxUrl;
            script.onload = () => {
                this.moamalatLightboxLoaded = true;
                resolve();
            };
            script.onerror = () => {
                reject(new Error('Failed to load Moamalat Lightbox script'));
            };
            document.head.appendChild(script);
        });
    },

    // #=== PAYMENT FLOW ===#

    /**
     * Process Moamalat implementation of the direct payment flow.
     *
     * @override method from payment.payment_form
     * @private
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option.
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {object} processingValues - The processing values of the transaction.
     * @return {void}
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'moamalat') {
            await this._super(...arguments);
            return;
        }

        // Show loading indicator
        const loadingEl = document.getElementById('o_moamalat_loading');
        const errorEl = document.getElementById('o_moamalat_error');
        if (loadingEl) loadingEl.style.display = 'block';
        if (errorEl) errorEl.style.display = 'none';

        try {
            // Check if Lightbox is loaded
            if (typeof Lightbox === 'undefined') {
                throw new Error(_t('Payment system not loaded. Please refresh the page.'));
            }

            const reference = processingValues['reference'] || processingValues['merchant_reference'];

            // Configure and show the Lightbox
            await this._showMoamalatLightbox({
                merchantId: processingValues['merchant_id'],
                terminalId: processingValues['terminal_id'],
                amount: processingValues['amount'],
                merchantReference: reference,
                secureHash: processingValues['secure_hash'],
                dateTimeLocalTrxn: processingValues['datetime_local'],
            });

        } catch (error) {
            if (loadingEl) loadingEl.style.display = 'none';
            this._displayErrorDialog(_t("Payment Error"), error.message || _t("An error occurred."));
            this._enableButton();
        }
    },

    /**
     * Show the Moamalat Lightbox for payment.
     *
     * @private
     * @param {Object} config - The Lightbox configuration
     * @return {Promise}
     */
    _showMoamalatLightbox(config) {
        return new Promise((resolve, reject) => {
            const self = this;
            let isCompleted = false;

            // Hide loading indicator
            const loadingEl = document.getElementById('o_moamalat_loading');
            if (loadingEl) loadingEl.style.display = 'none';

            // Configure the Lightbox
            Lightbox.Checkout.configure = {
                MID: config.merchantId,
                TID: config.terminalId,
                AmountTrxn: config.amount,
                MerchantReference: config.merchantReference,
                TrxDateTime: config.dateTimeLocalTrxn,
                SecureHash: config.secureHash,

                completeCallback: function(data) {
                    isCompleted = true;
                    self._onMoamalatComplete(data, config.merchantReference);
                    resolve(data);
                },

                errorCallback: function(error) {
                    self._onMoamalatError(error, config.merchantReference);
                    reject(error);
                },

                cancelCallback: function() {
                    // Only process cancel if complete wasn't called
                    if (!isCompleted) {
                        self._onMoamalatCancel(config.merchantReference);
                        reject(new Error(_t('Payment was cancelled.')));
                    }
                },
            };

            // Show the Lightbox
            try {
                Lightbox.Checkout.showLightbox();
            } catch (error) {
                reject(error);
            }
        });
    },

    /**
     * Handle successful payment completion from Lightbox.
     *
     * @private
     * @param {Object} data - The payment result data
     * @param {string} reference - The transaction reference
     */
    async _onMoamalatComplete(data, reference) {
        console.log('Moamalat payment completed:', data);

        try {
            // Send callback to server to update transaction
            const result = await rpc('/payment/moamalat/callback', {
                reference: reference,
                status: 'success',
                message: data.Message || 'Approved',
                SystemReference: data.SystemReference,
                NetworkReference: data.NetworkReference,
                ...data,
            });

            // Redirect to status page
            window.location.href = result.redirect_url || '/payment/status';

        } catch (error) {
            console.error('Error processing Moamalat callback:', error);
            // Still redirect to status page on error
            window.location.href = '/payment/status';
        }
    },

    /**
     * Handle payment error from Lightbox.
     *
     * @private
     * @param {Object} error - The error data
     * @param {string} reference - The transaction reference
     */
    async _onMoamalatError(error, reference) {
        console.error('Moamalat payment error:', error);

        try {
            await rpc('/payment/moamalat/callback', {
                reference: reference,
                status: 'error',
                message: error.Message || error.message || 'Payment failed',
                ...error,
            });
        } catch (rpcError) {
            console.error('Error sending error callback:', rpcError);
        }

        const errorEl = document.getElementById('o_moamalat_error');
        const errorMsgEl = document.getElementById('o_moamalat_error_message');
        if (errorEl) errorEl.style.display = 'block';
        if (errorMsgEl) errorMsgEl.textContent = error.Message || error.message || _t('Payment failed.');

        this._enableButton();
    },

    /**
     * Handle payment cancellation from Lightbox.
     *
     * @private
     * @param {string} reference - The transaction reference
     */
    async _onMoamalatCancel(reference) {
        console.log('Moamalat payment cancelled');

        try {
            await rpc('/payment/moamalat/callback', {
                reference: reference,
                status: 'cancelled',
                message: 'Payment was cancelled by user',
            });
        } catch (error) {
            console.error('Error sending cancel callback:', error);
        }

        this._enableButton();
        // Optionally redirect to status page
        window.location.href = '/payment/status';
    },

});
