"""
Amount handling utilities for economic operations.

Provides standardized rounding and validation for currency and crypto amounts.
All economic values should use these utilities to ensure consistency.
"""

from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from typing import Tuple

# Precision constants
CURRENCY_PRECISION = Decimal("0.01")  # 2 decimal places
CRYPTO_PRECISION = Decimal("0.00000001")  # 8 decimal places

# Minimum amounts
MIN_CURRENCY_AMOUNT = Decimal("0.01")
MIN_CRYPTO_AMOUNT = Decimal("0.00000001")


class AmountUtils:
    """Utility class for handling monetary amounts with proper rounding."""
    
    @staticmethod
    def round_currency(amount: Decimal) -> Decimal:
        """
        Round a currency amount to 2 decimal places using ROUND_HALF_UP.
        
        This is the standard financial rounding method where 0.005 rounds up to 0.01.
        Use this for regular amount inputs (not 'all'/'max').
        
        Args:
            amount: The amount to round
            
        Returns:
            Amount rounded to 2 decimal places
        """
        return amount.quantize(CURRENCY_PRECISION, rounding=ROUND_HALF_UP)
    
    @staticmethod
    def truncate_currency(amount: Decimal) -> Decimal:
        """
        Truncate a currency amount to 2 decimal places using ROUND_DOWN.
        
        Use this for 'all'/'max' keywords to ensure the amount never exceeds
        the available balance (prevents "insufficient funds" edge cases).
        
        Args:
            amount: The amount to truncate
            
        Returns:
            Amount truncated to 2 decimal places
        """
        return amount.quantize(CURRENCY_PRECISION, rounding=ROUND_DOWN)
    
    @staticmethod
    def round_crypto(amount: Decimal) -> Decimal:
        """
        Round a crypto amount to 8 decimal places using ROUND_HALF_UP.
        
        Use this for regular crypto amount inputs (not 'all'/'max').
        
        Args:
            amount: The amount to round
            
        Returns:
            Amount rounded to 8 decimal places
        """
        return amount.quantize(CRYPTO_PRECISION, rounding=ROUND_HALF_UP)
    
    @staticmethod
    def truncate_crypto(amount: Decimal) -> Decimal:
        """
        Truncate a crypto amount to 8 decimal places using ROUND_DOWN.
        
        Use this for 'all'/'max' keywords in crypto operations.
        
        Args:
            amount: The amount to truncate
            
        Returns:
            Amount truncated to 8 decimal places
        """
        return amount.quantize(CRYPTO_PRECISION, rounding=ROUND_DOWN)
    
    @staticmethod
    def validate_currency_minimum(amount: Decimal) -> Tuple[bool, str]:
        """
        Validate that a currency amount meets the minimum requirement.
        
        Args:
            amount: The amount to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if amount < MIN_CURRENCY_AMOUNT:
            return False, f"Amount must be at least {MIN_CURRENCY_AMOUNT}."
        return True, ""
    
    @staticmethod
    def validate_crypto_minimum(amount: Decimal) -> Tuple[bool, str]:
        """
        Validate that a crypto amount meets the minimum requirement.
        
        Args:
            amount: The amount to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if amount < MIN_CRYPTO_AMOUNT:
            return False, f"Amount must be at least {MIN_CRYPTO_AMOUNT}."
        return True, ""