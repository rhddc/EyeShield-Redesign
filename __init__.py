"""
EyeShield EMR - Segmented module structure
"""

from login import LoginWindow
from dashboard import EyeShieldApp
from screening import ScreeningPage
from patient_records import PatientRecordsPage
from reports import ReportsPage

__all__ = [
    'LoginWindow',
    'EyeShieldApp',
    'ScreeningPage',
    'PatientRecordsPage',
    'ReportsPage'
]
