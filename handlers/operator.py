"""Operator router.

Manual cement consumption is intentionally disabled in the integrated
CoBuilt workflow. Cement deductions are created atomically from Concrete
trip submissions by the integrated worker.
"""
from aiogram import Router

router = Router()
