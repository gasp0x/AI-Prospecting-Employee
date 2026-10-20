"""Launcher do app desktop MAA (empacotado via py2app)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.desktop import main

sys.exit(main())
