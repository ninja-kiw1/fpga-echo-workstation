"""F4 EF (Simpson biplane): the implementation lives in tcp_image_ui/ef_simpson.py so that the
host UI and the offline evaluation run the very same code. See that file for the method.
"""
import sys

from common import PLD

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
from ef_simpson import clean, diameters, ejection_fraction, to_isotropic, volumes  # noqa: E402,F401
