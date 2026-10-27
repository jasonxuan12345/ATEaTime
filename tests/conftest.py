"""Keep standalone subprocess imports and tests in this project."""
import os
from pathlib import Path
os.environ['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
