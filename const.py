# Part of Odoo. See LICENSE file for full copyright and licensing details.

# Currency mapping - Moamalat uses numeric currency codes
CURRENCY_MAPPING = {
    'LYD': '434',  # Libyan Dinar
    'EGP': '818',  # Egyptian Pound
    'USD': '840',  # US Dollar
    'EUR': '978',  # Euro
    'SAR': '682',  # Saudi Riyal
    'AED': '784',  # UAE Dirham
}

# Supported currencies
SUPPORTED_CURRENCIES = list(CURRENCY_MAPPING.keys())

# Transaction types
TXN_TYPE_SALE = '1'
TXN_TYPE_REFUND = '2'
TXN_TYPE_VOID_SALE = '3'
TXN_TYPE_VOID_REFUND = '4'

# Action codes
ACTION_CODE_APPROVED = '00'
