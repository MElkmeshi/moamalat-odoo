# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Payment Provider: Moamalat',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': "A payment provider for Moamalat Bank (Egypt/Libya).",
    'description': """
Moamalat Payment Provider
=========================
This module integrates Moamalat Bank payment gateway with Odoo.
It supports card payments through the Moamalat Lightbox interface.
    """,
    'author': 'Hajat',
    'depends': ['payment'],
    'data': [
        'security/ir.model.access.csv',
        'views/payment_provider_views.xml',
        'views/payment_moamalat_templates.xml',
        'data/payment_provider_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_moamalat/static/src/js/payment_form.js',
        ],
    },
    'application': False,
    'installable': True,
    'license': 'LGPL-3',
}
